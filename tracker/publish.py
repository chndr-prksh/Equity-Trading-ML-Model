"""Turn an engine Result into the static JSON the site reads."""
import datetime as dt
import json
import math
import re
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from . import stats
from .config import rules_dict
from .engine import REGIMES, SETUP_NAMES, trade_return

CHART_BARS = 320
SCREENER_COLS = ["symbol", "name", "sector", "close", "chg", "signal", "trend", "score", "rs", "tt", "setup",
                 "entry", "stop", "pnl", "days", "pivot", "from_high", "vol_x", "ret_1m", "ret_3m", "mcap",
                 "traded", "rated"]


def slug(symbol):
    return re.sub(r"[^A-Za-z0-9_-]", "_", symbol)


def _clean(x, nd=2):
    if x is None:
        return None
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    x = float(x)
    return round(x, nd) if math.isfinite(x) else None


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False))


def states(res):
    """Signal per symbol on the last bar, plus the trade behind it (if any)."""
    T, N = res.panel.C.shape
    last = T - 1
    elig = res.ind["eligible"].to_numpy()[last]
    c = res.panel.C.to_numpy()[last]
    piv = res.ind["pivot"].to_numpy()[last]
    tt_all = res.ind["tt_count"].to_numpy()[last] == 8
    down = res.ind["down"].to_numpy()[last]
    signal = np.where(elig, "", "NR").astype(object)
    with np.errstate(invalid="ignore"):
        watch = elig & tt_all & (c <= piv) & (c >= (1 - res.rules.watch_within) * piv) & (res.regime[last] > 0)
    signal[elig & down] = "AVOID"
    signal[watch] = "WATCH"
    signal[res.sell_event[last]] = "SELL"
    trade_of = {}
    for t in res.trades:
        j = t["j"]
        if t["exit"] is None and t["exit_signal"] is None:
            signal[j], trade_of[j] = "HOLD", t
        elif t["exit_signal"] == last:
            signal[j], trade_of[j] = "EXIT", t
    for j in res.pending:
        signal[j] = "BUY"
    return signal, trade_of


def trend_label(tt_count, c, s50, s200):
    if tt_count == 8:
        return "Strong uptrend"
    if c > s50 > s200:
        return "Uptrend"
    if c < s50 < s200:
        return "Downtrend"
    return "Neutral"


def _trade_json(res, t):
    d = res.panel.dates
    return {
        "setup": SETUP_NAMES[t["setup"]], "signal": d[t["signal"]].strftime("%Y-%m-%d"),
        "entry": d[t["entry"]].strftime("%Y-%m-%d"), "entry_px": _clean(t["entry_px"]), "stop": _clean(t["stop"]),
        "exit": d[t["exit"]].strftime("%Y-%m-%d") if t["exit"] is not None else None,
        "exit_px": _clean(t["exit_px"]), "reason": t["reason"],
        "ret": _clean(trade_return(t, res.market.round_trip_cost), 4),
    }


def build(res, uni, info, ledger_rows, launch, out_dir):
    out = Path(out_dir) / "data" / res.market.key
    if out.exists():
        shutil.rmtree(out)
    p, ind, rules = res.panel, res.ind, res.rules
    T, N = p.C.shape
    last = T - 1
    dates = p.dates
    C, H, L, O, V = (x.to_numpy() for x in (p.C, p.H, p.L, p.O, p.V))
    A = {k: v.to_numpy() for k, v in ind.items()}
    signal, trade_of = states(res)
    meta = uni.set_index("symbol").to_dict("index")
    elig = A["eligible"][last]

    def ret(n):
        with np.errstate(invalid="ignore", divide="ignore"):
            return C[last] / C[last - n] - 1 if last >= n else np.full(N, np.nan)

    chg, r1m, r3m = ret(1), ret(21), ret(63)
    with np.errstate(invalid="ignore", divide="ignore"):
        from_high = C[last] / A["hi52"][last] - 1
        vol_x = V[last] / A["avgv"][last - 1]
    stale = (last - p.last_real) > rules.stale_days

    # ---- screener: one row per stock that still trades
    rows, by_signal = [], {}
    for j, sym in enumerate(p.symbols):
        if stale[j] or not np.isfinite(C[last, j]):
            continue
        m = meta.get(sym, {})
        t = trade_of.get(j)
        rs, tt = A["rs"][last, j], int(A["tt_count"][last, j])
        rated = bool(elig[j])
        entry = stop = pnl = days = None
        setup = ""
        if signal[j] == "BUY":
            setup = SETUP_NAMES[res.pending[j]]
            stop = C[last, j] - rules.stop_atr * A["atr"][last, j]
        elif t is not None:
            setup, entry, stop = SETUP_NAMES[t["setup"]], t["entry_px"], t["stop"]
            pnl, days = C[last, j] / t["entry_px"] - 1, last - t["entry"]
        rows.append([
            sym, m.get("name", sym), m.get("sector", "Unclassified"), _clean(C[last, j]), _clean(chg[j], 4),
            signal[j], trend_label(tt, C[last, j], A["sma50"][last, j], A["sma200"][last, j]) if rated else "",
            _clean(50 * tt / 8 + 50 * rs / 99, 0) if rated and np.isfinite(rs) else None,
            _clean(rs, 0) if rated else None, tt if rated else None, setup,
            _clean(entry), _clean(stop), _clean(pnl, 4), days, _clean(A["pivot"][last, j]),
            _clean(from_high[j], 4), _clean(vol_x[j], 2), _clean(r1m[j], 4), _clean(r3m[j], 4),
            _clean(m.get("mcap"), 0), _clean(A["traded"][last, j], 0), rated,
        ])
        by_signal[signal[j]] = by_signal.get(signal[j], 0) + 1
    _write(out / "screener.json", {"asof": dates[last].strftime("%Y-%m-%d"), "cols": SCREENER_COLS, "rows": rows})

    # ---- per-stock detail for everything rated or involved in a signal
    trades_by = {}
    for t in res.trades:
        trades_by.setdefault(t["j"], []).append(t)
    lo = max(0, T - CHART_BARS)
    dstr = [d.strftime("%Y-%m-%d") for d in dates[lo:]]
    detail_count = 0
    for j, sym in enumerate(p.symbols):
        if stale[j] or not (elig[j] or signal[j] in ("BUY", "HOLD", "EXIT")):
            continue
        first = lo + int(np.argmax(np.isfinite(C[lo:, j])))
        k = first - lo
        _write(out / "s" / f"{slug(sym)}.json", {
            "symbol": sym, "d": dstr[k:],
            "o": [_clean(x) for x in O[first:, j]], "h": [_clean(x) for x in H[first:, j]],
            "l": [_clean(x) for x in L[first:, j]], "c": [_clean(x) for x in C[first:, j]],
            "v": [int(x) if np.isfinite(x) else 0 for x in V[first:, j]],
            "sma50": [_clean(x) for x in A["sma50"][first:, j]], "sma200": [_clean(x) for x in A["sma200"][first:, j]],
            "checks": [bool(cond[last, j]) for cond in res.tt],
            "levels": {"pivot": _clean(A["pivot"][last, j]), "hi52": _clean(A["hi52"][last, j]),
                       "lo52": _clean(A["lo52"][last, j]), "atr": _clean(A["atr"][last, j]),
                       "sma20": _clean(A["sma20"][last, j]), "sma150": _clean(A["sma150"][last, j])},
            "trades": [_trade_json(res, t) for t in trades_by.get(j, [])],
        })
        detail_count += 1

    # ---- breadth, sectors, regime
    n = min(252, T)
    e = A["eligible"][-n:]
    cnt = np.maximum(e.sum(1), 1)
    with np.errstate(invalid="ignore"):
        breadth = {
            "dates": [d.strftime("%Y-%m-%d") for d in dates[-n:]],
            "above50": [_clean(x, 1) for x in 100 * ((C[-n:] > A["sma50"][-n:]) & e).sum(1) / cnt],
            "above200": [_clean(x, 1) for x in 100 * ((C[-n:] > A["sma200"][-n:]) & e).sum(1) / cnt],
            "new_highs": [int(x) for x in ((H[-n:] >= A["hi52"][-n:]) & e).sum(1)],
            "new_lows": [int(x) for x in ((L[-n:] <= A["lo52"][-n:]) & e).sum(1)],
            "leaders": [int(x) for x in ((A["tt_count"][-n:] == 8) & e).sum(1)],
        }
    sec = pd.DataFrame({"sector": [meta.get(s, {}).get("sector", "Unclassified") for s in p.symbols],
                        "chg": chg, "r1m": r1m, "r3m": r3m, "rs": A["rs"][last],
                        "above50": C[last] > A["sma50"][last], "lead": A["tt_count"][last] == 8})[elig]
    sectors = [{"name": name, "n": int(len(g)), "chg": _clean(g.chg.median(), 4), "ret_1m": _clean(g.r1m.median(), 4),
                "ret_3m": _clean(g.r3m.median(), 4), "rs": _clean(g.rs.median(), 0),
                "above50": _clean(100 * g.above50.mean(), 0), "leaders": int(g.lead.sum())}
               for name, g in sec.groupby("sector") if len(g) >= 3]
    sectors.sort(key=lambda s: -(s["rs"] or 0))
    b = res.bench
    regime = {"state": REGIMES[int(res.regime[last])], "benchmark": info["benchmark"], "close": _clean(b.iloc[-1]),
              "chg": _clean(b.iloc[-1] / b.iloc[-2] - 1, 4), "sma50": _clean(b.rolling(50).mean().iloc[-1]),
              "sma200": _clean(b.rolling(200).mean().iloc[-1]),
              "history": {"dates": breadth["dates"], "close": [_clean(x) for x in b.iloc[-n:]],
                          "state": [int(x) for x in res.regime[-n:]]}}

    launch_ts = pd.Timestamp(launch)
    live = stats.trade_stats(res, since=launch_ts)
    summary = {
        "market": {"key": res.market.key, "name": res.market.name, "currency": res.market.currency,
                   "symbol": res.market.symbol, "min_price": res.market.min_price,
                   "min_traded_value": res.market.min_traded_value, "round_trip_cost": res.market.round_trip_cost},
        "asof": dates[last].strftime("%Y-%m-%d"),
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "history_from": dates[0].strftime("%Y-%m-%d"),
        "counts": {"listed": int(len(uni)), "tracked": len(rows), "rated": int(elig.sum()), "charts": detail_count,
                   **{k: by_signal.get(k, 0) for k in ("BUY", "HOLD", "EXIT", "SELL", "WATCH", "AVOID")}},
        "regime": regime, "breadth": breadth, "sectors": sectors,
        "backtest": {"trades": stats.trade_stats(res), "portfolio": stats.portfolio(res),
                     "from": dates[min(rules.min_bars, last)].strftime("%Y-%m-%d")},
        "live": {"since": launch, "trades": live, "events": len(ledger_rows)},
        "sources": {k: info.get(k) for k in ("price_source", "universe_source", "benchmark_source", "missing")},
        "adjustments": {"count": len(info.get("adjustments") or []),
                        "recent": sorted(info.get("adjustments") or [], key=lambda a: a["date"])[-15:],
                        "audit": info.get("audit")},
        "rules": rules_dict(),
    }
    _write(out / "summary.json", summary)
    _write(out / "ledger.json", {"since": launch, "rows": [
        [r["date"], r["symbol"], r["event"], r["detail"], _clean(float(r["price"])) if r["price"] else None,
         _clean(float(r["stop"])) if r["stop"] else None, int(r["rs"]) if r["rs"] else None] for r in ledger_rows[-3000:]]})
    return summary
