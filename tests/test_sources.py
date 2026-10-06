import numpy as np
import pandas as pd
import pytest

from tracker.sources import nasdaq, nse


@pytest.mark.parametrize("purpose,expected", [
    ("BONUS 1:1", 0.5), ("BONUS 1:2", 2 / 3), ("BONUS 3:1", 0.25),
    ("FVSPLT FRM RS 10 TO RE 1", 0.1), ("FVSPLT FRM RS 10 TO RS 2", 0.2), ("FV SPLIT RS.10/- TO RS.5/-", 0.5),
    ("BONUS 1:1 AND FVSPLT FRM RS 10 TO RS 5", 0.25),
    ("DIV - RS 6 PER SH", None), ("RGHTS 2:21 @PRM RS 748/-", None), ("DEMERGER", None), ("ANNUAL GENERAL MEETING", None),
])
def test_action_multiplier(purpose, expected):
    got = nse.action_multiplier(purpose)
    assert got is None if expected is None else got == pytest.approx(expected)


def _raw(symbol, closes, start="2026-01-01"):
    dates = pd.bdate_range(start, periods=len(closes))
    c = np.array(closes, dtype=float)
    return pd.DataFrame({"date": dates, "symbol": symbol, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                         "prev_close": np.r_[c[0], c[:-1]], "volume": 1000.0, "value": c * 1000})


def test_adjust_applies_declared_split_and_keeps_series_continuous():
    raw = _raw("AAA", [1000, 1010, 1020, 102, 103])
    actions = pd.DataFrame({"symbol": ["AAA"], "ex_date": [raw["date"][3]], "purpose": ["FVSPLT FRM RS 10 TO RE 1"]})
    adj, applied = nse.adjust(raw, actions)
    assert adj["close"].tolist() == pytest.approx([100, 101, 102, 102, 103])
    assert adj["volume"].tolist() == pytest.approx([10000, 10000, 10000, 1000, 1000])
    assert applied == [{"symbol": "AAA", "date": raw["date"][3].date().isoformat(), "factor": 0.1, "source": "declared"}]


def test_adjust_ignores_declared_action_the_price_did_not_follow():
    raw = _raw("AAA", [1000, 1010, 1020, 1015, 1030])                 # bonus announced but postponed
    actions = pd.DataFrame({"symbol": ["AAA"], "ex_date": [raw["date"][3]], "purpose": ["BONUS 1:1"]})
    adj, applied = nse.adjust(raw, actions)
    assert applied == [] and adj["close"].tolist() == pytest.approx(raw["close"].tolist())


def test_adjust_infers_unexplained_gap_but_not_listing_day_or_normal_moves():
    raw = pd.concat([_raw("DEM", [500, 505, 300, 303]), _raw("NORM", [100, 85, 110, 100])], ignore_index=True)
    raw.loc[raw.index[0], "prev_close"] = 250.0                       # listing day: issue price far from the open
    adj, applied = nse.adjust(raw, pd.DataFrame(columns=["symbol", "ex_date", "purpose"]))
    assert [(a["symbol"], a["source"]) for a in applied] == [("DEM", "gap")]
    assert adj[adj.symbol == "DEM"]["close"].tolist() == pytest.approx([500 * 300 / 505, 300, 300, 303])
    assert adj[adj.symbol == "NORM"]["close"].tolist() == [100, 85, 110, 100]


def test_two_splits_compound():
    raw = _raw("AAA", [800, 400, 404, 202, 204])
    actions = pd.DataFrame({"symbol": ["AAA", "AAA"], "ex_date": [raw["date"][1], raw["date"][3]], "purpose": ["BONUS 1:1", "BONUS 1:1"]})
    adj, _ = nse.adjust(raw, actions)
    assert adj["close"].tolist() == pytest.approx([200, 200, 202, 202, 204])


@pytest.mark.parametrize("name,common", [
    ("Apple Inc. Common Stock", True), ("Western Midstream Partners LP Common Units Representing LP Interests", True),
    ("Alibaba Group Holding Limited American Depositary Shares", True),
    ("Cheche Group Inc. Warrant", False), ("Armada Acquisition Corp. III Units", False),
    ("Bruker Corporation 6.375% Mandatory Convertible Preferred Stock", False), ("Bank Nova Scotia Halifax Pfd 3", False),
    ("Some Acquisition Corp Rights", False), ("TPG Mortgage Investment Trust Inc. 9.500% Senior Notes", False),
])
def test_us_common_stock_filter(name, common):
    assert (nasdaq._NOT_COMMON.search(name) is None) == common


def test_renamed_symbol_keeps_its_history_and_its_split():
    raw = _raw("OLD", [1000, 1010, 101, 102])
    raw.loc[raw.index[2:], "symbol"] = "NEW"                           # renamed on the ex-date
    actions = pd.DataFrame({"symbol": ["OLD"], "ex_date": [raw["date"][2]], "purpose": ["FVSPLT FRM RS 10 TO RE 1"]})
    renames = pd.DataFrame({"old": ["OLD"], "new": ["NEW"], "date": [raw["date"][2]]})
    adj, applied = nse.adjust(raw, actions, renames)
    assert adj["symbol"].unique().tolist() == ["NEW"] and adj["close"].tolist() == pytest.approx([100, 101, 101, 102])
    assert len(applied) == 1


def test_split_on_a_day_without_trading_applies_at_the_next_session():
    raw = _raw("AAA", [1000, 1010, 101, 102])
    ex = raw["date"][2] - pd.Timedelta(days=1)
    raw = raw.drop(index=1).reset_index(drop=True)                     # no bar on the day before; ex-date falls in the gap
    raw.loc[1, "prev_close"] = 1000.0
    actions = pd.DataFrame({"symbol": ["AAA"], "ex_date": [ex], "purpose": ["FVSPLT FRM RS 10 TO RE 1"]})
    adj, applied = nse.adjust(raw, actions)
    assert adj["close"].tolist() == pytest.approx([100, 101, 102]) and applied[0]["source"] == "declared"


def test_yahoo_history_gives_up_quickly_when_throttled(monkeypatch):
    from tracker.sources import yahoo
    calls = []

    def refused(ysym, years=5):
        calls.append(ysym)
        raise RuntimeError("429")

    monkeypatch.setattr(yahoo, "history_one", refused)
    bars, missing = yahoo.history([f"S{i}" for i in range(500)], "us", workers=4)
    assert len(missing) == 500 and bars.empty and len(calls) < 60
