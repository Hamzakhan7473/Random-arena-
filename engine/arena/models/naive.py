from __future__ import annotations

from datetime import datetime

import pandas as pd

from arena.models.common import forecasts_for_horizons
from arena.schema import HORIZONS, QUANTILE_KEYS, Forecast
from arena.series import history_through


class NaiveLast:
    """Every quantile is the last observed close."""

    model_id = "naive-last"
    min_points = 1

    def forecast(self, symbol: str, prices: pd.Series, issued_at: datetime) -> list[Forecast]:
        last = float(history_through(prices, issued_at, self.min_points).iloc[-1])
        quantiles = {key: last for key in QUANTILE_KEYS}
        return forecasts_for_horizons(
            self.model_id,
            symbol,
            issued_at,
            {horizon: quantiles for horizon in HORIZONS},
        )
