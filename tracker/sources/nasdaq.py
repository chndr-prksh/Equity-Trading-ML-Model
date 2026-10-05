"""Nasdaq.com public API (no key).

* `universe()` - one call listing every stock on NASDAQ/NYSE/NYSE American with sector,
  industry, country and market cap.
* `history()` - per-symbol split-adjusted daily bars; ~3 req/s, used only when Yahoo is down.
"""
import datetime as dt
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from .. import http

log = logging.getLogger(__name__)
API = "https://api.nasdaq.com/api"
HEADERS = {"Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com",
           "Referer": "https://www.nasdaq.com/"}

# Not common equity: warrants, SPAC units/rights, preferreds, baby bonds.
_NOT_COMMON = re.compile(
    r"\b(warrants?|rights?|preferred|preference|pfd|notes|debentures?|subordinated)\b|%|(?<!common )\bunits?\b", re.I)


def _num(s):
    try:
        return float(str(s).replace("$", "").replace(",", "").replace("%", ""))
    except ValueError:
        return float("nan")


def universe():
    r = http.get(f"{API}/screener/stocks?tableonly=true&limit=25&offset=0&download=true", headers=HEADERS)
    rows = r.json()["data"]["rows"]
    out = []
    for x in rows:
        sym, name = x["symbol"].strip(), x["name"].strip()
        if "^" in sym or _NOT_COMMON.search(name) or x.get("industry") == "Blank Checks":
            continue
        short = re.sub(r"\s+(Common Stock|Common Shares|Ordinary Shares|Class [A-Z] .*|American Depositary .*|"
                       r"Depositary .*|Common Units.*|\(.*\)).*$", "", name).strip(" ,-")
        out.append({"symbol": sym, "name": short or name, "sector": x.get("sector") or "Unclassified",
                    "industry": x.get("industry") or "", "mcap": _num(x.get("marketCap")) or None,
                    "country": x.get("country") or ""})
    df = pd.DataFrame(out).drop_duplicates("symbol")
    if len(df) < 3000:
        raise RuntimeError(f"Nasdaq screener returned only {len(df)} common stocks")
    return df


def history_one(symbol, years=5):
    today = dt.date.today()
    frm = today - dt.timedelta(days=365 * years)
    url = (f"{API}/quote/{symbol.replace('/', '.')}/historical?assetclass=stocks"
           f"&fromdate={frm}&todate={today}&limit=9999")
    data = http.get(url, headers=HEADERS, timeout=30, retries=2).json().get("data") or {}
    rows = (data.get("tradesTable") or {}).get("rows") or []
    if not rows:
        return None
    df = pd.DataFrame({
        "date": pd.to_datetime([x["date"] for x in rows], format="%m/%d/%Y"),
        "open": [_num(x["open"]) for x in rows], "high": [_num(x["high"]) for x in rows],
        "low": [_num(x["low"]) for x in rows], "close": [_num(x["close"]) for x in rows],
        "volume": [_num(x["volume"]) for x in rows],
    }).dropna(subset=["open", "high", "low", "close"])
    return df.sort_values("date").drop_duplicates("date", keep="last")


def history(symbols, years=5, workers=6):
    def one(sym):
        try:
            return sym, history_one(sym, years)
        except Exception:
            return sym, None

    frames, missing = [], []
    with ThreadPoolExecutor(workers) as ex:
        for sym, df in ex.map(one, symbols):
            if df is None:
                missing.append(sym)
            else:
                frames.append(df.assign(symbol=sym))
    log.info("nasdaq: %d ok, %d missing", len(frames), len(missing))
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["date", "open", "high", "low", "close", "volume", "symbol"])
    return out, missing
