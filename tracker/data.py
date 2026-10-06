"""Load a market: universe, split-adjusted daily bars and a benchmark, with a fallback source for each."""
import logging
from pathlib import Path

import pandas as pd

from .config import MARKETS
from .sources import nasdaq, nse, nse_mcp, yahoo

log = logging.getLogger(__name__)


def proxy_benchmark(bars):
    """Equal-weight index of everything we have. Used only if the real index cannot be fetched."""
    wide = bars.pivot(index="date", columns="symbol", values="close")
    ret = wide.pct_change(fill_method=None).clip(-0.25, 0.25).mean(axis=1).fillna(0)
    return (1 + ret).cumprod() * 100


def _benchmark(market, bars, official=None):
    for name, fn in (("official", lambda: official), ("yahoo", lambda: yahoo.benchmark(market.benchmark_yahoo, market.history_years))):
        try:
            s = fn()
            if s is not None and len(s) > 250:
                return s, market.benchmark, name
        except Exception as e:
            log.warning("%s benchmark from %s failed: %s", market.key, name, e)
    return proxy_benchmark(bars), "Equal-weight market proxy", "derived"


def load_us(market, cache, limit=None):
    uni = nasdaq.universe()
    if limit:
        uni = uni.sort_values("mcap", ascending=False).head(limit)
    # Nasdaq leaves market cap blank for closed-end funds and some share classes.
    # Done before the bulk price download: Yahoo refuses new crumbs right after a burst.
    blank = uni.loc[~(uni["mcap"] > 0), "symbol"].tolist()
    if blank:
        try:
            caps = yahoo.market_caps(blank, "us")
            uni["mcap"] = uni["mcap"].where(uni["mcap"] > 0, uni["symbol"].map(caps))
            log.info("us: filled %d of %d missing market caps from Yahoo", len(caps), len(blank))
        except Exception as e:
            log.warning("us: could not fill missing market caps: %s", e)
    symbols = uni["symbol"].tolist()
    bars, missing = yahoo.history(symbols, "us", market.history_years)
    source = "Yahoo Finance"
    if len(missing) > 0.3 * len(symbols):
        log.warning("yahoo missing %d of %d US symbols; falling back to Nasdaq", len(missing), len(symbols))
        more, missing = nasdaq.history(missing, market.history_years)
        bars = pd.concat([bars, more], ignore_index=True)
        source = "Yahoo Finance + Nasdaq"
    bench, bench_name, bench_src = _benchmark(market, bars)
    info = {"price_source": source, "universe_source": "Nasdaq screener", "benchmark": bench_name,
            "benchmark_source": bench_src, "missing": len(missing), "adjustments": [], "audit": None}
    return uni, bars, bench, info


def load_nse(market, cache, limit=None):
    uni = nse.universe()
    info = {"universe_source": "NSE EQUITY_L", "adjustments": [], "audit": None, "missing": 0}
    official_bench = None
    try:
        store = nse.Store(Path(cache) / "nse")
        store.update(market.history_years)
        raw = store.prices()
        try:
            renames = nse.symbol_changes()
        except Exception as e:
            log.warning("NSE symbol-change list unavailable: %s", e)
            renames = None
        bars, applied = nse.adjust(raw, store.actions(), renames)
        bars = bars[bars["symbol"].isin(uni["symbol"])]
        applied = [a for a in applied if a["symbol"] in set(uni["symbol"])]
        idx = store.indices()
        official_bench = idx[idx["name"] == market.benchmark].set_index("date")["close"].sort_index()
        uni = uni.merge(store.mcap(), on="symbol", how="left")
        info.update(price_source="NSE daily bhavcopy archive", adjustments=applied)
    except Exception as e:
        log.warning("NSE archive failed (%s); falling back to Yahoo", e)
        bars, missing = yahoo.history(uni["symbol"].tolist(), "nse", market.history_years)
        if len(missing) > 0.5 * len(uni):
            raise RuntimeError("both NSE archive and Yahoo failed for NSE prices")
        uni["mcap"] = None
        info.update(price_source="Yahoo Finance", missing=len(missing))
    if limit:
        top = bars.assign(tv=bars["close"] * bars["volume"]).groupby("symbol")["tv"].median().nlargest(limit).index
        uni, bars = uni[uni["symbol"].isin(top)], bars[bars["symbol"].isin(top)]
    bench, bench_name, bench_src = _benchmark(market, bars, official_bench)
    info.update(benchmark=bench_name, benchmark_source="NSE archive" if bench_src == "official" else bench_src)
    return uni, bars, bench, info


def audit_nse(info, symbols, panel):
    """Cross-check our split/bonus factors for `symbols` against NSE's MCP server. Never fatal."""
    if not symbols or info.get("price_source") != "NSE daily bhavcopy archive":
        return
    try:
        # only history the engine actually uses: the panel drops bars before a long trading hole
        first = {s: panel.C[s].first_valid_index().date().isoformat() for s in symbols}
        issues, checked = nse_mcp.audit_adjustments(
            info["adjustments"], symbols, panel.dates[0].date(), panel.dates[-1].date(), first)
        info["audit"] = {"checked": checked, "issues": issues}
        if issues:
            log.warning("NSE MCP audit: %d adjustment disagreements, e.g. %s", len(issues), issues[:3])
    except Exception as e:
        log.warning("NSE MCP audit skipped: %s", e)


def load(key, cache=".cache", limit=None):
    market = MARKETS[key]
    return (market,) + {"us": load_us, "nse": load_nse}[key](market, cache, limit)
