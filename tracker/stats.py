"""Honest scorekeeping: per-trade statistics versus the benchmark, and a capped-portfolio simulation."""
import numpy as np
import pandas as pd

from .engine import SETUP_NAMES, trade_r, trade_return


def _agg(df):
    if not len(df):
        return {"n": 0}
    wins, losses = df.ret[df.ret > 0], df.ret[df.ret <= 0]
    return {
        "n": int(len(df)),
        "win_rate": round(float((df.ret > 0).mean()), 4),
        "avg": round(float(df.ret.mean()), 4),
        "median": round(float(df.ret.median()), 4),
        "avg_win": round(float(wins.mean()), 4) if len(wins) else None,
        "avg_loss": round(float(losses.mean()), 4) if len(losses) else None,
        "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else None,
        "avg_r": round(float(df.r.mean()), 2),
        "avg_days": round(float(df.days.mean()), 1),
        "bench_avg": round(float(df.bench.mean()), 4),
        "excess": round(float((df.ret - df.bench).mean()), 4),
    }


def closed_frame(res, since=None):
    """Closed trades as a frame with net return, R multiple, holding days and benchmark return over the same days."""
    b = res.bench.to_numpy()
    dates = res.panel.dates
    rows = []
    for t in res.trades:
        if t["exit"] is None or (since is not None and dates[t["signal"]] < since):
            continue
        rows.append({
            "setup": SETUP_NAMES[t["setup"]], "year": int(dates[t["entry"]].year), "reason": t["reason"],
            "ret": trade_return(t, res.market.round_trip_cost), "r": trade_r(t),
            "days": t["exit"] - t["entry"], "bench": b[t["exit"]] / b[t["entry"]] - 1,
        })
    return pd.DataFrame(rows, columns=["setup", "year", "reason", "ret", "r", "days", "bench"])


def trade_stats(res, since=None):
    df = closed_frame(res, since)
    return {
        "all": _agg(df),
        "by_setup": {k: _agg(g) for k, g in df.groupby("setup")},
        "by_year": {str(k): _agg(g) for k, g in df.groupby("year")},
        "by_reason": {k: _agg(g) for k, g in df.groupby("reason")},
    }


def _curve_metrics(values, days_per_year=252):
    v = np.asarray(values, dtype=float)
    years = max(len(v) / days_per_year, 1e-9)
    peak = np.maximum.accumulate(v)
    return {"total": round(float(v[-1] / v[0] - 1), 4), "cagr": round(float((v[-1] / v[0]) ** (1 / years) - 1), 4),
            "max_drawdown": round(float((v / peak - 1).min()), 4)}


def portfolio(res):
    """Equal-weight, at most `max_positions` at once, strongest relative strength first when signals compete.

    Cash earns nothing. Costs are charged on exit. This is a simulation on today's listings
    (survivorship-biased), not a track record.
    """
    rules, cost = res.rules, res.market.round_trip_cost
    c = res.panel.C.to_numpy()
    T = len(res.panel.dates)
    start = rules.min_bars
    if T <= start + 20:
        return None
    entries, exits = {}, {}
    for i, t in enumerate(res.trades):
        if t["entry"] >= start:
            entries.setdefault(t["entry"], []).append(i)
    for day in entries:
        entries[day].sort(key=lambda i: -np.nan_to_num(res.trades[i]["rs"]))
    cash, held, equity, exposure = 1.0, {}, [], []
    last_equity = 1.0
    for d in range(start, T):
        for i in exits.pop(d, []):
            cash += held.pop(i) * res.trades[i]["exit_px"] * (1 - cost)
        for i in entries.get(d, []):
            alloc = min(last_equity / rules.max_positions, cash)
            if len(held) >= rules.max_positions or alloc < 0.2 * last_equity / rules.max_positions:
                break
            t = res.trades[i]
            held[i] = alloc / t["entry_px"]
            cash -= alloc
            if t["exit"] is not None:
                exits.setdefault(t["exit"], []).append(i)
        invested = sum(sh * c[d, res.trades[i]["j"]] for i, sh in held.items())
        last_equity = cash + invested
        equity.append(last_equity)
        exposure.append(invested / last_equity)
    bench = res.bench.to_numpy()[start:]
    bench = bench / bench[0]
    dates = res.panel.dates[start:]
    step = slice(None, None, 5)
    idx = sorted(set(range(len(dates))[step]) | {len(dates) - 1})
    return {
        "dates": [dates[i].strftime("%Y-%m-%d") for i in idx],
        "strategy": [round(equity[i], 4) for i in idx],
        "benchmark": [round(float(bench[i]), 4) for i in idx],
        "metrics": {"strategy": _curve_metrics(equity), "benchmark": _curve_metrics(bench),
                    "avg_exposure": round(float(np.mean(exposure)), 3)},
    }
