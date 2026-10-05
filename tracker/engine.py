"""Signal engine: indicators -> ratings -> entry triggers -> trade replay.

Everything is computed on (dates x symbols) matrices so the same code rates one stock or
the whole market. A signal fires on a day's close; the replay assumes the fill happens at
the next session's open, and stops are checked against the following days' lows.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import RULES, Market, Rules

BREAKOUT, PULLBACK = 1, 2
SETUP_NAMES = {BREAKOUT: "breakout", PULLBACK: "pullback"}
REGIMES = {2: "uptrend", 1: "caution", 0: "downtrend"}
MAX_HOLE_DAYS = 60


@dataclass
class Panel:
    dates: pd.DatetimeIndex
    symbols: list
    O: pd.DataFrame
    H: pd.DataFrame
    L: pd.DataFrame
    C: pd.DataFrame
    V: pd.DataFrame
    last_real: np.ndarray  # index of each symbol's last traded bar


def build_panel(long_df):
    """Long [symbol,date,open,high,low,close,volume] -> aligned matrices on the market's calendar."""
    df = long_df.dropna(subset=["close"]).sort_values(["symbol", "date"])
    # A stock that vanished for months (suspended, moved to another segment) comes back as a
    # different series: prices in between are unknown and may have been rescaled. Keep the latest run.
    hole = df.groupby("symbol")["date"].diff().dt.days > MAX_HOLE_DAYS
    df = df[hole.groupby(df["symbol"]).transform(lambda h: h[::-1].cumsum()[::-1]) == 0]
    counts = df.groupby("date").size()
    dates = pd.DatetimeIndex(counts[counts >= 0.1 * counts.max()].index).sort_values()  # drop stray half-sessions
    df = df[df["date"].isin(dates)]
    sj, symbols = pd.factorize(df["symbol"], sort=True)
    di = dates.searchsorted(df["date"].to_numpy())
    T, N = len(dates), len(symbols)

    def mat(col):
        a = np.full((T, N), np.nan)
        a[di, sj] = df[col].to_numpy(dtype=float)
        return a

    c = mat("close")
    real = ~np.isnan(c)
    last_real = np.where(real.any(0), T - 1 - real[::-1].argmax(0), -1)
    C = pd.DataFrame(c, index=dates, columns=symbols).ffill()
    # A day without trades is carried as a flat, zero-volume bar so rolling windows keep their length.
    frames = {}
    for name, col in (("O", "open"), ("H", "high"), ("L", "low")):
        a = mat(col)
        frames[name] = pd.DataFrame(np.where(np.isnan(a), C.to_numpy(), a), index=dates, columns=symbols)
    v = mat("volume")
    V = pd.DataFrame(np.where(real, np.nan_to_num(v), 0.0), index=dates, columns=symbols).where(C.notna())
    return Panel(dates, list(symbols), frames["O"], frames["H"], frames["L"], C, V, last_real)


@dataclass
class Result:
    panel: Panel
    market: Market
    rules: Rules
    ind: dict                       # name -> DataFrame
    tt: list                        # 8 boolean arrays, the trend-template conditions
    regime: np.ndarray              # per date: 2 uptrend, 1 caution, 0 downtrend
    bench: pd.Series
    trades: list = field(default_factory=list)
    pending: dict = field(default_factory=dict)   # symbol index -> setup code, signalled on the last bar
    sell_event: np.ndarray = None


def wilder(df, n):
    return df.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def regime_of(bench):
    b50, b200 = bench.rolling(50).mean(), bench.rolling(200).mean()
    above = (bench > b50).astype(int) + (bench > b200).astype(int)
    return above.where(b200.notna(), 1).to_numpy()   # unknown history -> neutral


def compute(panel, bench, market, rules=RULES):
    O, H, L, C, V = panel.O, panel.H, panel.L, panel.C, panel.V
    r = rules
    sma = {n: C.rolling(n).mean() for n in (20, 50, 150, 200)}
    hi52, lo52 = H.rolling(252).max(), L.rolling(252).min()
    prev_c = C.shift(1)
    tr = np.maximum(H - L, np.maximum((H - prev_c).abs(), (L - prev_c).abs()))
    atr = wilder(tr, r.atr_days)
    avgv = V.rolling(50).mean()
    traded = (C * V).rolling(r.liquidity_days).median()

    eligible = (C.shift(r.min_bars).notna() & (C >= market.min_price) & (traded >= market.min_traded_value)).to_numpy().copy()
    stale = (len(panel.dates) - 1 - panel.last_real) > r.stale_days
    eligible[-1, stale] = False

    # IBD-style relative strength: last quarter counts double, ranked 1-99 across eligible stocks.
    rs_raw = 2 * C / C.shift(63) + C / C.shift(126) + C / C.shift(189) + C / C.shift(252)
    rs = (rs_raw.where(eligible).rank(axis=1, pct=True) * 98 + 1).round()

    c = C.to_numpy()
    s50, s150, s200 = (sma[n].to_numpy() for n in (50, 150, 200))
    with np.errstate(invalid="ignore"):
        tt = [
            (c > s150) & (c > s200),
            s150 > s200,
            s200 > sma[200].shift(r.sma200_slope_days).to_numpy(),
            (s50 > s150) & (s50 > s200),
            c > s50,
            c >= r.low_52w_mult * lo52.to_numpy(),
            c >= r.high_52w_mult * hi52.to_numpy(),
            rs.to_numpy() >= r.rs_min,
        ]
        tt_count = np.sum(tt, axis=0)
        tt_all = (tt_count == 8) & eligible
        calm = atr.to_numpy() <= r.max_atr_pct * c

        bench = bench.reindex(panel.dates).ffill().bfill()
        regime = regime_of(bench)
        risk_on = (regime > 0)[:, None]

        pivot = H.rolling(r.pivot_days).max().shift(1)
        h, l, v = H.to_numpy(), L.to_numpy(), V.to_numpy()
        piv = pivot.to_numpy()
        breakout = (tt_all & calm & risk_on & (c > piv) & (v >= r.breakout_vol_mult * avgv.shift(1).to_numpy())
                    & (c >= (h + l) / 2) & (c <= r.max_extension * s50))
        s20 = sma[20].to_numpy()
        pullback = (tt_all & calm & risk_on & ~breakout & (c <= piv)
                    & (L.rolling(r.pullback_lookback).min().to_numpy() <= r.pullback_touch * s20)
                    & (c > s20) & (c > H.shift(1).to_numpy()))
        setup = np.where(breakout, BREAKOUT, np.where(pullback, PULLBACK, 0)).astype(np.int8)

        down = (c < s50) & (s50 < s200)
        was_down = pd.DataFrame(down).rolling(10).max().shift(1).fillna(0).to_numpy().astype(bool)
        sell_event = down & ~was_down & eligible

    ind = {"sma20": sma[20], "sma50": sma[50], "sma150": sma[150], "sma200": sma[200], "hi52": hi52, "lo52": lo52,
           "atr": atr, "avgv": avgv, "traded": traded, "rs": rs, "pivot": pivot,
           "tt_count": pd.DataFrame(tt_count, index=C.index, columns=C.columns),
           "eligible": pd.DataFrame(eligible, index=C.index, columns=C.columns),
           "setup": pd.DataFrame(setup, index=C.index, columns=C.columns),
           "down": pd.DataFrame(down, index=C.index, columns=C.columns)}
    res = Result(panel, market, rules, ind, tt, regime, bench, sell_event=sell_event)
    res.trades, res.pending = replay(panel, setup, atr.to_numpy(), rs.to_numpy(), s50, rules)
    return res


def replay(panel, setup, atr, rs, sma50, rules=RULES):
    """Walk every entry trigger forward to its exit. Returns (trades, pending signals from the last bar).

    Exits: the protective stop (fixed at entry - stop_atr x ATR, filled intraday), or a close
    below the 50-day average (filled at the next open).
    """
    o, l, c = (x.to_numpy() for x in (panel.O, panel.L, panel.C))
    T = len(panel.dates)
    trades, pending = [], {}
    for j in np.flatnonzero(setup.any(axis=0)):
        sig_days = np.flatnonzero(setup[:, j])
        oj, lj, cj, aj, mj = o[:, j].tolist(), l[:, j].tolist(), c[:, j].tolist(), atr[:, j].tolist(), sma50[:, j].tolist()
        last_real = int(panel.last_real[j])
        free_from = 0
        for t in sig_days.tolist():
            if t < free_from:
                continue                      # already in a trade
            if t == T - 1:
                pending[int(j)] = int(setup[t, j])
                break
            e = t + 1
            entry = oj[e]
            risk = rules.stop_atr * aj[t]
            if not (entry > 0 and risk > 0) or e > last_real:
                continue
            stop = entry - risk
            x = exit_px = reason = exit_signal = None
            for d in range(e, T):
                if d > last_real + rules.stale_days:
                    x, exit_px, reason, exit_signal = d, cj[d], "halted", d
                    break
                if lj[d] <= stop:
                    x, exit_px, reason, exit_signal = d, (min(oj[d], stop) if d > e else stop), "stop", d
                    break
                if cj[d] < mj[d]:
                    exit_signal, reason = d, "trend"
                    if d + 1 < T:
                        x, exit_px = d + 1, oj[d + 1]
                    break
            trades.append({
                "j": int(j), "setup": int(setup[t, j]), "signal": t, "entry": e, "entry_px": entry,
                "stop": stop, "exit": x, "exit_px": exit_px, "reason": reason, "exit_signal": exit_signal,
                "rs": rs[t, j], "last": cj[T - 1] if x is None else exit_px,
            })
            if x is None:
                break
            free_from = x + 1
    return trades, pending


def trade_return(t, cost=0.0):
    return t["last"] / t["entry_px"] - 1 - (cost if t["exit"] is not None else 0)


def trade_r(t):
    return (t["last"] - t["entry_px"]) / (t["entry_px"] - t["stop"])
