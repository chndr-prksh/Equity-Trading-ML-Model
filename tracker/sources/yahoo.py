"""Yahoo Finance chart API (unofficial, no key). One request returns a symbol's full daily history.

The `quote` OHLC values are already split-adjusted but not dividend-adjusted, which is what a
price-level signal engine wants: stops and pivots stay on prices people actually saw.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import pandas as pd
import requests

from .. import http

log = logging.getLogger(__name__)
HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")


def to_yahoo(symbol, market):
    if market == "nse":
        return symbol + ".NS"
    return symbol.replace("/", "-").replace(".", "-")


def _parse(result, now=None):
    ts = result.get("timestamp")
    if not ts:
        return None
    q = result["indicators"]["quote"][0]
    meta = result["meta"]
    df = pd.DataFrame({
        "date": pd.to_datetime(ts, unit="s", utc=True).tz_convert(meta["exchangeTimezoneName"]).tz_localize(None).normalize(),
        "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"], "volume": q["volume"],
    }).dropna(subset=["open", "high", "low", "close"])
    # A bar for a session that has not closed yet is partial; signals are end-of-day only.
    end = (meta.get("currentTradingPeriod") or {}).get("regular", {}).get("end")
    now = now or time.time()
    if end and len(df) and now < end and ts[-1] >= end - 86400:
        df = df.iloc[:-1]
    df = df.drop_duplicates("date", keep="last")
    return df if len(df) else None


def history_one(ysym, years=5):
    for i, host in enumerate(HOSTS):
        url = f"https://{host}/v8/finance/chart/{quote(ysym)}?range={years}y&interval=1d&events=split"
        try:
            r = http.get(url, timeout=20, retries=2, ok404=True)
        except requests.RequestException:
            if i == len(HOSTS) - 1:
                raise
            continue
        if r is None:
            return None
        res = (r.json().get("chart") or {}).get("result")
        return _parse(res[0]) if res else None


def history(symbols, market, years=5, workers=8):
    """Return (long DataFrame[symbol,date,open,high,low,close,volume], list of symbols with no data)."""
    def one(sym):
        try:
            return sym, history_one(to_yahoo(sym, market), years), None
        except Exception as e:  # network/parse failure for one symbol must not sink the run
            return sym, None, e

    frames, missing, errors = [], [], 0
    with ThreadPoolExecutor(workers) as ex:
        for sym, df, err in ex.map(one, symbols):
            if df is None:
                missing.append(sym)
                errors += err is not None
            else:
                frames.append(df.assign(symbol=sym))
    log.info("yahoo %s: %d ok, %d missing (%d errors)", market, len(frames), len(missing), errors)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["date", "open", "high", "low", "close", "volume", "symbol"])
    return out, missing


def benchmark(ysym, years=5):
    df = history_one(ysym, years)
    if df is None:
        raise RuntimeError(f"no benchmark data for {ysym}")
    return df.set_index("date")["close"]
