"""Append-only signal log.

Every BUY / EXIT / SELL the engine emits is written here once and never rewritten, and the
file is committed by the daily job. Git history therefore proves when each signal was
published, which is the difference between a track record and a backtest.
"""
import csv
import datetime as dt
from pathlib import Path

import numpy as np

from .engine import SETUP_NAMES

FIELDS = ["date", "symbol", "event", "detail", "price", "stop", "rs", "logged_at"]


def read(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def events_for(res, d):
    """All events whose signal fired on the close of date index `d`."""
    p = res.panel
    c = p.C.to_numpy()
    rs = res.ind["rs"].to_numpy()
    out = []

    def row(j, event, detail, price, stop=""):
        r = rs[d, j]
        out.append({"date": p.dates[d].strftime("%Y-%m-%d"), "symbol": p.symbols[j], "event": event,
                    "detail": detail, "price": round(float(price), 2),
                    "stop": round(float(stop), 2) if stop != "" else "", "rs": int(r) if np.isfinite(r) else ""})

    atr = res.ind["atr"].to_numpy()
    for t in res.trades:
        if t["signal"] == d:
            row(t["j"], "BUY", SETUP_NAMES[t["setup"]], c[d, t["j"]], t["stop"])
        if t["exit_signal"] == d:
            row(t["j"], "EXIT", t["reason"], t["exit_px"] if t["reason"] != "trend" else c[d, t["j"]])
    if d == len(p.dates) - 1:
        for j, setup in res.pending.items():
            row(j, "BUY", SETUP_NAMES[setup], c[d, j], c[d, j] - res.rules.stop_atr * atr[d, j])
    for j in np.flatnonzero(res.sell_event[d]):
        row(int(j), "SELL", "trend breakdown", c[d, j])
    return out


def update(res, path, now=None):
    """Append events not yet logged. Returns (all rows, launch date)."""
    path = Path(path)
    rows = read(path)
    dates = res.panel.dates
    last = len(dates) - 1
    if rows:
        # Every session since launch within the last month, so a run that missed a day or had
        # incomplete data is healed by the next one. Existing lines are never touched; a late
        # line is recognisable by its logged_at.
        launch = min(r["date"] for r in rows)
        todo = [d for d in range(max(0, last - 21), last + 1) if dates[d].strftime("%Y-%m-%d") >= launch]
    else:
        todo = [last]
    seen = {(r["date"], r["symbol"], r["event"]) for r in rows}
    stamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%MZ")
    new = [dict(e, logged_at=stamp) for d in todo for e in events_for(res, d)
           if (e["date"], e["symbol"], e["event"]) not in seen]
    if new or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if fresh:
                w.writeheader()
            w.writerows(new)
    rows += new
    launch = min((r["date"] for r in rows), default=dates[last].strftime("%Y-%m-%d"))
    return rows, launch
