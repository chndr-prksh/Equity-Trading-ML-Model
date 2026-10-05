import numpy as np
import pandas as pd
import pytest

from tracker.config import MARKETS


def make_bars(n_symbols=30, n_days=620, seed=7):
    """Random walks with assorted drifts: enough leaders, laggards and noise to trigger every signal."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n_days)
    frames = []
    for i in range(n_symbols):
        drift = rng.normal(0.0006, 0.0012)
        ret = rng.normal(drift, 0.02, n_days)
        close = 100 * np.exp(np.cumsum(ret))
        open_ = close * (1 + rng.normal(0, 0.004, n_days))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.008, n_days)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.008, n_days)))
        vol = rng.lognormal(13, 0.6, n_days).round()
        frames.append(pd.DataFrame({"symbol": f"S{i:02d}", "date": dates, "open": open_, "high": high,
                                    "low": low, "close": close, "volume": vol}))
    return pd.concat(frames, ignore_index=True)


@pytest.fixture(scope="session")
def bars():
    return make_bars()


@pytest.fixture(scope="session")
def market():
    return MARKETS["us"]
