from __future__ import annotations

import math
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.stats import norm

from arena.errors import InsufficientHistory
from arena.models.common import forecasts_for_horizons
from arena.schema import HORIZON_DELTA, HORIZONS, QUANTILE_KEYS, QUANTILES, Forecast
from arena.series import history_through


class GarchVol:
    """GARCH(1,1) on percent log returns. Horizon variance is the sum of step variances."""

    model_id = "garch11"
    min_points = 120

    def forecast(self, symbol: str, prices: pd.Series, issued_at: datetime) -> list[Forecast]:
        from arch import arch_model

        series = history_through(prices, issued_at, self.min_points)
        returns = np.diff(np.log(series.to_numpy(dtype=np.float64))) * 100.0
        model = arch_model(returns, mean="Constant", vol="GARCH", p=1, q=1, dist="normal")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fitted = model.fit(disp="off", show_warning=False)
        mu = float(fitted.params["mu"])
        last = float(series.iloc[-1])
        by_horizon: dict[str, dict[str, float]] = {}
        for horizon in HORIZONS:
            hours = int(HORIZON_DELTA[horizon].total_seconds() // 3600)
            forecast = fitted.forecast(horizon=hours, reindex=False)
            step_variance = forecast.variance.iloc[-1].to_numpy(dtype=np.float64)
            variance = float(np.sum(step_variance))
            if not math.isfinite(mu) or not math.isfinite(variance) or variance < 0:
                raise InsufficientHistory(f"garch variance was not finite for {horizon}")
            mean = mu * hours
            std = math.sqrt(variance)
            by_horizon[horizon] = {
                key: float(last * math.exp((mean + std * float(norm.ppf(level))) / 100.0))
                for key, level in zip(QUANTILE_KEYS, QUANTILES, strict=True)
            }
        return forecasts_for_horizons(self.model_id, symbol, issued_at, by_horizon)
