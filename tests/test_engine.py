import json
import math

import numpy as np
import pandas as pd

from tracker import data, engine, ledger, publish, stats
from tracker.config import RULES


def run(bars, market):
    panel = engine.build_panel(bars)
    return engine.compute(panel, data.proxy_benchmark(bars), market)


def test_signals_and_trades_are_generated(bars, market):
    res = run(bars, market)
    assert len(res.trades) > 20
    assert {t["reason"] for t in res.trades if t["exit"] is not None} <= {"stop", "trend", "halted"}


def test_trade_mechanics(bars, market):
    res = run(bars, market)
    o, l, c = (x.to_numpy() for x in (res.panel.O, res.panel.L, res.panel.C))
    s50 = res.ind["sma50"].to_numpy()
    for t in res.trades:
        j = t["j"]
        assert t["entry"] == t["signal"] + 1                      # fill on the session after the signal
        assert t["entry_px"] == o[t["entry"], j]
        assert t["stop"] < t["entry_px"]
        if t["reason"] == "stop":
            assert l[t["exit"], j] <= t["stop"]
            assert t["exit_px"] <= t["stop"] + 1e-9               # never a better fill than the stop
            assert (l[t["entry"]:t["exit"], j] > t["stop"]).all()  # and not hit earlier
        if t["reason"] == "trend" and t["exit"] is not None:
            assert c[t["exit_signal"], j] < s50[t["exit_signal"], j]
            assert t["exit"] == t["exit_signal"] + 1 and t["exit_px"] == o[t["exit"], j]


def test_one_position_per_symbol(bars, market):
    res = run(bars, market)
    by = {}
    for t in res.trades:
        by.setdefault(t["j"], []).append(t)
    for ts in by.values():
        for a, b in zip(ts, ts[1:]):
            assert a["exit"] is not None and b["signal"] > a["exit"]


def test_no_lookahead(bars, market):
    """Signals up to day t must not change when later data arrives."""
    full = run(bars, market)
    cut = full.panel.dates[-120]
    part = run(bars[bars["date"] <= cut], market)
    n = len(part.panel.dates)
    assert np.array_equal(part.ind["setup"].to_numpy(), full.ind["setup"].to_numpy()[:n])
    assert np.array_equal(part.ind["rs"].to_numpy(), full.ind["rs"].to_numpy()[:n], equal_nan=True)
    key = lambda t: (t["j"], t["signal"], t["exit"], t["exit_px"])
    done_early = [key(t) for t in full.trades if t["exit"] is not None and t["exit"] < n]
    assert done_early == [key(t) for t in part.trades if t["exit"] is not None]


def test_rs_is_a_percentile_rank(bars, market):
    rs = run(bars, market).ind["rs"].iloc[-1].dropna()
    assert rs.between(1, 99).all() and rs.max() > 90 and rs.min() < 10


def test_regime_blocks_buys(bars, market):
    panel = engine.build_panel(bars)
    falling = pd.Series(np.linspace(200, 100, len(panel.dates)), index=panel.dates)
    res = engine.compute(panel, falling, market)
    assert (res.regime[250:] == 0).all()
    assert not res.ind["setup"].to_numpy()[250:].any()


def test_ledger_is_append_only_and_idempotent(bars, market, tmp_path):
    res = run(bars, market)
    path = tmp_path / "us.csv"
    rows1, launch1 = ledger.update(res, path)
    text1 = path.read_text()
    rows2, launch2 = ledger.update(res, path)
    assert path.read_text() == text1 and launch1 == launch2 and len(rows1) == len(rows2)
    # a later run adds only later sessions and leaves earlier lines untouched
    earlier = run(bars[bars["date"] <= res.panel.dates[-6]], market)
    p2 = tmp_path / "b.csv"
    ledger.update(earlier, p2)
    before = p2.read_text()
    ledger.update(res, p2)
    assert p2.read_text().startswith(before)
    assert all(r["date"] > earlier.panel.dates[-1].strftime("%Y-%m-%d") for r in ledger.read(p2)[before.count("\n") - 1:])


def test_publish_writes_valid_json(bars, market, tmp_path):
    res = run(bars, market)
    uni = pd.DataFrame({"symbol": res.panel.symbols, "name": res.panel.symbols, "sector": "Test", "mcap": 1e9})
    info = {"benchmark": "proxy", "benchmark_source": "derived", "price_source": "synthetic", "universe_source": "synthetic"}
    rows, launch = ledger.update(res, tmp_path / "ledger.csv")
    summary = publish.build(res, uni, info, rows, launch, tmp_path)
    out = tmp_path / "data" / "us"
    scr = json.loads((out / "screener.json").read_text())      # json.loads rejects NaN written as bare tokens? be explicit:
    assert "NaN" not in (out / "screener.json").read_text() and "NaN" not in (out / "summary.json").read_text()
    assert scr["cols"] == publish.SCREENER_COLS and all(len(r) == len(scr["cols"]) for r in scr["rows"])
    counts = summary["counts"]
    sig = [r[scr["cols"].index("signal")] for r in scr["rows"]]
    assert counts["HOLD"] == sig.count("HOLD") and counts["BUY"] == sig.count("BUY")
    held = next(r for r in scr["rows"] if r[5] == "HOLD")
    detail = json.loads((out / "s" / f"{held[0]}.json").read_text())
    assert len(detail["d"]) == len(detail["c"]) == len(detail["sma50"]) and len(detail["checks"]) == 8
    assert detail["trades"][-1]["exit"] is None
    pf = summary["backtest"]["portfolio"]
    assert len(pf["dates"]) == len(pf["strategy"]) == len(pf["benchmark"]) and math.isclose(pf["benchmark"][0], 1.0)


def test_portfolio_never_exceeds_position_cap(bars, market):
    res = run(bars, market)
    pf = stats.portfolio(res)
    assert 0 <= pf["metrics"]["avg_exposure"] <= 1.0001 and min(pf["strategy"]) > 0
    assert RULES.max_positions == 20


def test_history_before_a_long_trading_hole_is_dropped(bars):
    one = bars[bars["symbol"] == "S00"]
    cut = one["date"].iloc[300]
    holed = pd.concat([bars[bars["symbol"] != "S00"], one[(one["date"] < cut) | (one["date"] > cut + pd.Timedelta(days=120))]])
    panel = engine.build_panel(holed)
    first = panel.C["S00"].first_valid_index()
    assert first > cut + pd.Timedelta(days=120)
    assert panel.C["S01"].first_valid_index() == bars["date"].min()
