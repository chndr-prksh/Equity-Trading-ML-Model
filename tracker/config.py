"""Every tunable number lives here so the methodology page and the code cannot drift apart.

The parameters are textbook defaults (Minervini trend template, IBD-style RS weights, an
ATR stop and the 50-day line) and are the same for every market. They are not optimised.
The one choice made after seeing results was the exit style: a close below the 50-day
average replaced an ATR trailing stop because it did better in both markets.
"""
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Rules:
    # trend template
    low_52w_mult: float = 1.25      # close at least 25% above the 52-week low
    high_52w_mult: float = 0.75     # close within 25% of the 52-week high
    sma200_slope_days: int = 21     # 200-day average must be higher than a month ago
    rs_min: int = 70                # relative-strength rating floor
    # entries
    pivot_days: int = 50            # breakout = close above the prior 50-day high
    breakout_vol_mult: float = 1.5  # ...on at least 1.5x the 50-day average volume
    max_extension: float = 1.25     # skip if already >25% above the 50-day average
    max_atr_pct: float = 0.06       # skip if the average daily range exceeds 6% of price (stop would be >15% away)
    pullback_lookback: int = 5      # pullback = tagged the 20-day average in the last 5 bars
    pullback_touch: float = 1.02
    watch_within: float = 0.05      # "watch" = within 5% below the pivot
    # exits
    atr_days: int = 14
    stop_atr: float = 2.5           # protective stop: entry - 2.5 ATR; trend exit: close below the 50-day average
    # history required before a stock can be rated
    min_bars: int = 252
    liquidity_days: int = 20
    stale_days: int = 5
    # portfolio simulation
    max_positions: int = 20


@dataclass(frozen=True)
class Market:
    key: str
    name: str
    currency: str
    symbol: str                 # currency symbol for display
    min_price: float            # eligibility floor
    min_traded_value: float     # median daily traded value floor (local currency)
    round_trip_cost: float      # assumed cost per completed trade, as a fraction
    benchmark: str              # display name
    benchmark_yahoo: str
    tz: str
    history_years: int = 5


RULES = Rules()

MARKETS = {
    "us": Market("us", "United States", "USD", "$", 5.0, 2_000_000, 0.001,
                 "S&P 500", "^GSPC", "America/New_York"),
    "nse": Market("nse", "India (NSE)", "INR", "₹", 20.0, 20_000_000, 0.003,
                  "Nifty 500", "^CRSLDX", "Asia/Kolkata"),
}


def rules_dict():
    return asdict(RULES)
