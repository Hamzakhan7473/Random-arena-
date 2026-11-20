from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.stats import norm

from arena.errors import InsufficientHistory
from arena.models.common import forecasts_for_horizons
from arena.schema import HORIZON_DELTA, HORIZONS, QUANTILE_KEYS, QUANTILES, Forecast
from arena.series import history_through


class RandomWalk:
    """Gaussian random walk on log price. Variance grows with the horizon."""

    model_id = "random-walk"
    min_points = 49

    def forecast(self, symbol: str, prices: pd.Series, issued_at: datetime) -> list[Forecast]:
        series = history_through(prices, issued_at, self.min_points)
        log_price = np.log(series.to_numpy(dtype=np.float64))
        returns = np.diff(log_price)
        mu = float(np.mean(returns))
        sigma = float(np.std(returns, ddof=1))
        if not math.isfinite(mu) or not math.isfinite(sigma) or sigma < 0:
            raise InsufficientHistory("random-walk moments were not finite")
        last = float(series.iloc[-1])
        by_horizon: dict[str, dict[str, float]] = {}
        for horizon in HORIZONS:
            hours = HORIZON_DELTA[horizon].total_seconds() / 3600
            mean = mu * hours
            std = sigma * math.sqrt(hours)
            by_horizon[horizon] = {
                key: float(last * math.exp(mean + std * float(norm.ppf(level))))
                for key, level in zip(QUANTILE_KEYS, QUANTILES, strict=True)
            }
        return forecasts_for_horizons(self.model_id, symbol, issued_at, by_horizon)
