"""Daily job: python -m tracker.run --market nse --market us --out public --ledger ledger"""
import argparse
import datetime as dt
import logging
import sys
import time

from . import data, engine, ledger, publish

log = logging.getLogger("tracker")


def run_market(key, out, ledger_dir, cache, limit=None):
    t0 = time.time()
    market, uni, bars, bench, info = data.load(key, cache, limit)
    panel = engine.build_panel(bars)
    age = (dt.date.today() - panel.dates[-1].date()).days
    if age > 6:
        raise RuntimeError(f"{key}: latest bar is {panel.dates[-1].date()}, {age} days old")
    res = engine.compute(panel, bench, market)
    rated = int(res.ind["eligible"].iloc[-1].sum())
    if rated < (20 if limit else 300):
        raise RuntimeError(f"{key}: only {rated} stocks passed the data checks; refusing to publish")
    if key == "nse":
        signal, _ = publish.states(res)
        # fresh signals first: the audit is capped, so spend it where a bad price would mislead today
        rank = {"BUY": 0, "EXIT": 1, "HOLD": 2}
        active = sorted((s for s, sig in zip(panel.symbols, signal) if sig in rank),
                        key=lambda s: rank[signal[panel.symbols.index(s)]])
        data.audit_nse(info, active, panel)
    rows, launch = ledger.update(res, f"{ledger_dir}/{key}.csv")
    summary = publish.build(res, uni, info, rows, launch, out)
    c = summary["counts"]
    log.info("%s %s: %d rated, BUY %d / HOLD %d / EXIT %d / SELL %d, regime %s, %.0fs", key, summary["asof"],
             c["rated"], c["BUY"], c["HOLD"], c["EXIT"], c["SELL"], summary["regime"]["state"], time.time() - t0)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--market", action="append", choices=["us", "nse"], required=True)
    ap.add_argument("--out", default="public")
    ap.add_argument("--ledger", default="ledger")
    ap.add_argument("--cache", default=".cache")
    ap.add_argument("--limit", type=int, help="only the N largest/most liquid stocks (smoke tests)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    failed = []
    for key in args.market:
        try:
            run_market(key, args.out, args.ledger, args.cache, args.limit)
        except Exception:
            log.exception("market %s failed; its previously published data is left in place", key)
            failed.append(key)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
