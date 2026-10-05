"""NSE archives (official, no key, no cookies).

One zip per trading day (`PRddmmyy.zip`) carries the whole cash market: OHLCV for every
security, index closes, the corporate-action calendar and market caps. Prices are raw, so
this module also turns the corporate-action calendar into split/bonus adjustment factors.

The raw days are kept in a local parquet store so a daily run downloads a single file.
"""
import datetime as dt
import io
import json
import logging
import math
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from .. import http

log = logging.getLogger(__name__)
ARCH = "https://nsearchives.nseindia.com"
SERIES = ("EQ", "BE", "BZ")          # main-board equity: normal, trade-for-trade, non-compliant
INDICES = ("Nifty 500", "Nifty 50")
PRICE_COLS = ["open", "high", "low", "close", "prev_close", "volume", "value"]


def _csv(url):
    return pd.read_csv(io.StringIO(http.get(url).text), skipinitialspace=True)


def universe():
    """All listed main-board equities, with industry where NSE publishes one (Nifty Total Market, 750 names)."""
    eq = _csv(f"{ARCH}/content/equities/EQUITY_L.csv")
    eq.columns = [c.strip() for c in eq.columns]
    eq = eq[eq["SERIES"].isin(SERIES)]
    df = pd.DataFrame({"symbol": eq["SYMBOL"].str.strip(), "name": eq["NAME OF COMPANY"].str.strip()})
    df["name"] = df["name"].str.replace(r"\s+(Limited|Ltd\.?)$", "", regex=True, flags=re.I)
    try:
        ind = _csv(f"{ARCH}/content/indices/ind_niftytotalmarket_list.csv")
        df = df.merge(ind.rename(columns={"Symbol": "symbol", "Industry": "sector"})[["symbol", "sector"]],
                      on="symbol", how="left")
    except Exception as e:
        log.warning("NSE industry list unavailable: %s", e)
        df["sector"] = None
    df["sector"] = df["sector"].fillna("Unclassified")
    df["industry"] = ""
    if len(df) < 1500:
        raise RuntimeError(f"NSE equity list returned only {len(df)} symbols")
    return df.drop_duplicates("symbol")


def symbol_changes():
    r = http.get(f"{ARCH}/content/equities/symbolchange.csv")
    df = pd.read_csv(io.StringIO(r.text), header=None, names=["name", "old", "new", "date"],
                     skipinitialspace=True, encoding_errors="replace")
    df["date"] = pd.to_datetime(df["date"], format="%d-%b-%Y", errors="coerce")
    return df.dropna(subset=["date"])[["old", "new", "date"]]


# ---------------------------------------------------------------- daily zip

def _member(zf, prefix):
    for n in zf.namelist():
        if n.lower().startswith(prefix) and n.lower().endswith(".csv"):
            return zf.read(n).decode("latin-1")
    return None


def _parse_day(content, day):
    zf = zipfile.ZipFile(io.BytesIO(content))
    pd_txt = _member(zf, "pd")
    if pd_txt is None:
        raise ValueError(f"no price file in PR zip for {day}")
    raw = pd.read_csv(io.StringIO(pd_txt), skipinitialspace=True, dtype=str)
    raw.columns = [c.strip() for c in raw.columns]
    for c in raw.columns:
        raw[c] = raw[c].str.strip()
    num = {"OPEN_PRICE": "open", "HIGH_PRICE": "high", "LOW_PRICE": "low", "CLOSE_PRICE": "close",
           "PREV_CL_PR": "prev_close", "NET_TRDQTY": "volume", "NET_TRDVAL": "value"}
    for src, dst in num.items():
        raw[dst] = pd.to_numeric(raw[src], errors="coerce")
    raw["date"] = pd.Timestamp(day)

    eq = raw[(raw["MKT"] == "N") & raw["SERIES"].isin(SERIES)].rename(columns={"SYMBOL": "symbol"})
    prices = eq[["date", "symbol"] + PRICE_COLS].dropna(subset=["open", "high", "low", "close"])
    prices = prices[prices["close"] > 0].drop_duplicates("symbol")

    idx = raw[(raw["MKT"] == "Y") & raw["SECURITY"].isin(INDICES)]
    idx = idx.rename(columns={"SECURITY": "name"})[["date", "name", "close"]]

    ca = pd.DataFrame(columns=["symbol", "ex_date", "purpose"])
    bc_txt = _member(zf, "bc")
    if bc_txt:
        bc = pd.read_csv(io.StringIO(bc_txt), dtype=str, on_bad_lines="skip")
        bc.columns = [c.strip() for c in bc.columns]
        bc = bc[bc["SERIES"].str.strip().isin(SERIES)]
        ex = bc["EX_DT"].str.strip()
        iso = ex.str.contains("-", na=False)
        ex_date = pd.to_datetime(ex.where(iso), format="%Y-%m-%d", errors="coerce").fillna(
            pd.to_datetime(ex.where(~iso), format="%d/%m/%Y", errors="coerce"))
        ca = pd.DataFrame({"symbol": bc["SYMBOL"].str.strip(), "ex_date": ex_date,
                           "purpose": bc["PURPOSE"].str.strip().str.upper()}).dropna()

    mcap = None
    mc_txt = _member(zf, "mcap")
    if mc_txt:
        mc = pd.read_csv(io.StringIO(mc_txt), dtype=str, on_bad_lines="skip")
        mc.columns = [c.strip() for c in mc.columns]
        mcap = pd.DataFrame({"symbol": mc["Symbol"].str.strip(),
                             "mcap": pd.to_numeric(mc["Market Cap(Rs.)"].str.strip(), errors="coerce")})
        mcap = mcap[mc["Series"].str.strip().isin(SERIES).values].dropna().drop_duplicates("symbol")
    return prices, idx, ca, mcap


def fetch_day(day):
    """Parsed (prices, index closes, corporate actions, market caps) for one date, or None on a market holiday."""
    r = http.get(f"{ARCH}/archives/equities/bhavcopy/pr/PR{day:%d%m%y}.zip", ok404=True)
    return None if r is None else _parse_day(r.content, day)


# ---------------------------------------------------------------- local store

class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _read(self, name, cols):
        p = self.root / f"{name}.parquet"
        return pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=cols)

    def prices(self):
        return self._read("raw", ["date", "symbol"] + PRICE_COLS)

    def indices(self):
        return self._read("idx", ["date", "name", "close"])

    def actions(self):
        return self._read("ca", ["symbol", "ex_date", "purpose"])

    def mcap(self):
        return self._read("mcap", ["symbol", "mcap"])

    def meta(self):
        p = self.root / "meta.json"
        return json.loads(p.read_text()) if p.exists() else {"closed": []}

    def update(self, years=5, today=None, workers=6):
        """Download every missing trading day up to today. Returns the latest stored date."""
        today = today or dt.date.today()
        prices, idx, ca, meta = self.prices(), self.indices(), self.actions(), self.meta()
        have = set(pd.to_datetime(prices["date"]).dt.date.unique()) if len(prices) else set()
        closed = {dt.date.fromisoformat(d) for d in meta["closed"]}
        start = today - dt.timedelta(days=int(365.25 * years))
        want = [d for d in (start + dt.timedelta(n) for n in range((today - start).days + 1))
                if d.weekday() < 5 and d not in have and d not in closed]
        if not want:
            return max(have) if have else None
        log.info("nse: fetching %d daily files (%s .. %s)", len(want), want[0], want[-1])

        def one(d):
            try:
                return d, fetch_day(d), None
            except Exception as e:
                return d, None, e

        new_p, new_i, new_c, mcap, failed = [], [], [], None, []
        with ThreadPoolExecutor(workers) as ex:
            for d, res, err in ex.map(one, want):
                if err is not None:
                    failed.append(d)
                elif res is None:
                    # A 404 for a recent date may just mean "not published yet": only remember old ones.
                    if (today - d).days > 4:
                        closed.add(d)
                else:
                    new_p.append(res[0]); new_i.append(res[1]); new_c.append(res[2])
                    if res[3] is not None:
                        mcap = res[3]
        if failed:
            log.warning("nse: %d daily files failed, e.g. %s", len(failed), failed[:3])
            if len(failed) > max(3, len(want) // 20):
                raise RuntimeError(f"NSE archive unreachable for {len(failed)} of {len(want)} days")
        if new_p:
            prices = pd.concat([prices] + new_p, ignore_index=True).drop_duplicates(["date", "symbol"])
            idx = pd.concat([idx] + new_i, ignore_index=True).drop_duplicates(["date", "name"])
            ca = pd.concat([ca] + new_c, ignore_index=True).drop_duplicates()
            cutoff = pd.Timestamp(start)
            prices[prices["date"] >= cutoff].to_parquet(self.root / "raw.parquet", index=False)
            idx[idx["date"] >= cutoff].to_parquet(self.root / "idx.parquet", index=False)
            ca.to_parquet(self.root / "ca.parquet", index=False)
            if mcap is not None:
                mcap.to_parquet(self.root / "mcap.parquet", index=False)
        (self.root / "meta.json").write_text(json.dumps({"closed": sorted(d.isoformat() for d in closed)}))
        return pd.to_datetime(prices["date"]).max().date() if len(prices) else None


# ---------------------------------------------------------------- adjustment

_BONUS = re.compile(r"BONUS\D{0,12}(\d+)\s*:\s*(\d+)")
_SPLIT = re.compile(r"(?:FV\s*SPL\w*|FACE VALUE SPLIT|SPLIT)\D*?(\d+(?:\.\d+)?)\D+?(\d+(?:\.\d+)?)")


def action_multiplier(purpose):
    """Price multiplier applied to every bar before the ex-date. None if the action does not rescale price."""
    m = 1.0
    b = _BONUS.search(purpose)
    if b and int(b[1]) > 0 and int(b[2]) > 0:
        m *= int(b[2]) / (int(b[1]) + int(b[2]))      # "BONUS a:b" = a new shares for every b held
    s = _SPLIT.search(purpose)
    if s and float(s[1]) > 0 and float(s[2]) > 0:
        m *= float(s[2]) / float(s[1])                # face value from x to y
    return None if m == 1.0 else m


# Main-board price bands cap a normal session at +/-20%, so a larger opening gap that no
# recorded action explains (demergers, capital reductions) is treated as a rescaling too.
GAP_DOWN, GAP_UP = 0.72, 1.45


def adjust(prices, actions, renames=None):
    """Return (adjusted long frame, list of applied adjustments)."""
    df = prices.copy()
    if renames is not None and len(renames):
        first = df["date"].min()
        mapping = dict(renames[renames["date"] >= first][["old", "new"]].values)
        actions = actions.copy()
        for _ in range(3):  # follow chains A -> B -> C
            df["symbol"] = df["symbol"].map(lambda s: mapping.get(s, s))
            actions["symbol"] = actions["symbol"].map(lambda s: mapping.get(s, s))
        df = df.sort_values("date").drop_duplicates(["date", "symbol"], keep="last")
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)

    declared = {}
    for sym, ex, purpose in actions[["symbol", "ex_date", "purpose"]].drop_duplicates().itertuples(index=False):
        m = action_multiplier(purpose)
        if m:
            declared.setdefault((sym, pd.Timestamp(ex)), {})[purpose] = m
    declared = {k: math.prod(v.values()) for k, v in declared.items()}

    gap = (df["open"] / df["prev_close"]).to_numpy()
    sym_arr, date_arr = df["symbol"].to_numpy(), df["date"].to_numpy()
    mult = np.ones(len(df))
    applied = []
    cand = set(np.flatnonzero((gap < GAP_DOWN) | (gap > GAP_UP)))
    first_row = {s: i for i, s in reversed(list(enumerate(sym_arr)))}
    for key, m in declared.items():
        # the first session on or after the ex-date (the stock may not trade on the day itself)
        lo = first_row.get(key[0])
        if lo is None:
            continue
        hi = lo + int(np.searchsorted(sym_arr[lo:], key[0], side="right"))
        i = lo + int(np.searchsorted(date_arr[lo:hi], np.datetime64(key[1])))
        if i >= hi or (pd.Timestamp(date_arr[i]) - key[1]).days > 45 or not np.isfinite(gap[i]):
            continue
        # Trust a declared action only if the open actually moved towards it (actions get postponed).
        if abs(math.log(gap[i] / m)) < min(abs(math.log(gap[i])), 0.2):
            mult[i] = m
            applied.append({"symbol": key[0], "date": pd.Timestamp(date_arr[i]).date().isoformat(), "factor": round(m, 6), "source": "declared"})
            cand.discard(i)
    for i in cand:
        if i == 0 or sym_arr[i - 1] != sym_arr[i]:
            continue  # listing day: the "previous close" is the issue price
        if np.isfinite(gap[i]) and gap[i] > 0:
            mult[i] = gap[i]
            applied.append({"symbol": sym_arr[i], "date": pd.Timestamp(date_arr[i]).date().isoformat(),
                            "factor": round(float(gap[i]), 6), "source": "gap"})

    # factor for a bar = product of the multipliers of all later events of the same symbol
    df["_m"] = mult
    rev = df.iloc[::-1].groupby("symbol", sort=False)["_m"]
    df["_f"] = (rev.cumprod() / df["_m"].iloc[::-1]).iloc[::-1]
    for c in ("open", "high", "low", "close"):
        df[c] = df[c] * df["_f"]
    df["volume"] = df["volume"] / df["_f"]
    return df.drop(columns=["_m", "_f", "prev_close", "value"]), applied
