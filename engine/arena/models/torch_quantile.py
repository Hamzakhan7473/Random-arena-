from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from arena.models.common import forecasts_for_horizons
from arena.schema import HORIZON_DELTA, HORIZONS, QUANTILE_KEYS, QUANTILES, Forecast
from arena.series import history_through


class TorchQuantile:
    """Linear quantile model trained, at issue time, on returns already realized.

    Predicted log returns are clipped to [-2, 2] so a diverging fit still
    produces finite prices. Quantiles are sorted before they are sealed.
    """

    model_id = "torch-quantile"
    lookback = 24
    epochs = 250
    learning_rate = 0.05
    min_points = lookback + 24 + 30

    def forecast(self, symbol: str, prices: pd.Series, issued_at: datetime) -> list[Forecast]:
        import torch

        torch.manual_seed(0)
        torch.set_num_threads(1)
        series = history_through(prices, issued_at, self.min_points)
        log_price = np.log(series.to_numpy(dtype=np.float64))
        returns = np.diff(log_price)
        last = float(series.iloc[-1])
        levels = torch.tensor(QUANTILES, dtype=torch.float32).view(1, -1)
        by_horizon: dict[str, dict[str, float]] = {}
        count = len(log_price)
        for horizon in HORIZONS:
            hours = int(HORIZON_DELTA[horizon].total_seconds() // 3600)
            features: list[np.ndarray] = []
            targets: list[float] = []
            # A label at origin t uses the close at t+hours, which must already exist.
            for origin in range(self.lookback, count - hours):
                features.append(returns[origin - self.lookback : origin])
                targets.append(float(log_price[origin + hours] - log_price[origin]))
            inputs = torch.tensor(np.stack(features), dtype=torch.float32)
            labels = torch.tensor(targets, dtype=torch.float32).unsqueeze(1)
            torch.manual_seed(0)
            layer = torch.nn.Linear(self.lookback, len(QUANTILES))
            optimizer = torch.optim.Adam(layer.parameters(), lr=self.learning_rate)
            for _ in range(self.epochs):
                optimizer.zero_grad()
                predicted = layer(inputs)
                residual = labels - predicted
                loss = torch.maximum(levels * residual, (levels - 1) * residual).mean()
                loss.backward()
                optimizer.step()
            with torch.no_grad():
                latest = torch.tensor(returns[-self.lookback :], dtype=torch.float32).unsqueeze(0)
                raw = layer(latest).numpy()[0]
            # Clip before sorting so a diverging fit still emits finite prices.
            ordered = np.sort(np.clip(raw, -2.0, 2.0))
            by_horizon[horizon] = {
                key: float(last * np.exp(value))
                for key, value in zip(QUANTILE_KEYS, ordered, strict=True)
            }
        return forecasts_for_horizons(self.model_id, symbol, issued_at, by_horizon)
