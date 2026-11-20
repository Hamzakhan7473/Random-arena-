"""Chronos-Bolt-small adapter.

Checkpoint: ``amazon/chronos-bolt-small`` via ``chronos-forecasting``.

Official usage (model card + ``ChronosBoltPipeline.predict``):

    from chronos import BaseChronosPipeline
    pipeline = BaseChronosPipeline.from_pretrained(
        "amazon/chronos-bolt-small", device_map="cpu", torch_dtype=torch.float32,
    )
    forecast = pipeline.predict(inputs, prediction_length=24)
    # shape [num_series, num_quantiles, prediction_length]

Native quantiles, from the checkpoint ``chronos_config.quantiles`` and the
pipeline docstring, are ``[0.1, 0.2, ..., 0.9]``. Arena levels 0.1, 0.5, 0.9
are native. 0.25 and 0.75 are linear interpolations in quantile-level space
between 0.2/0.3 and 0.7/0.8 (see ``foundation.interpolate_quantiles``).

``predict_quantiles`` on Chronos-Bolt interpolates with ``torch.quantile`` on
an augmented native grid; this adapter maps the native ``predict`` tensor
instead so the interpolation is the same linear rule used for TimesFM.

One forward pass at horizon 24; steps 1, 4, 24 are rows 0, 3, 23.
Weights live in the Hugging Face Hub cache; the pipeline is a process singleton.
"""

from __future__ import annotations

import pandas as pd
import torch

from arena.models.base import Forecaster
from arena.models.foundation import (
    PREDICTION_LENGTH,
    interpolate_quantiles,
    prepare_forecast,
    quote_from_horizon_quantiles,
)

HF_ID = "amazon/chronos-bolt-small"


class ChronosBoltSmall(Forecaster):
    name = "chronos-bolt-small"
    version = HF_ID
    _pipeline = None

    def forecast(
        self,
        history: pd.Series,
        horizons: list[int],
        quantiles: list[float],
    ) -> dict[int, dict[float, float]]:
        context, hours, levels = prepare_forecast(history, horizons, quantiles)
        pipeline = self._load()
        native_levels = [float(q) for q in pipeline.quantiles]
        context_tensor = torch.as_tensor(context, dtype=torch.float32)
        with torch.inference_mode():
            # Official predict layout: [num_series, num_quantiles, prediction_length].
            forecast = pipeline.predict(
                context_tensor,
                prediction_length=PREDICTION_LENGTH,
            )
        native = forecast[0].detach().cpu().numpy().T  # (H, Q_native)
        mapped = interpolate_quantiles(native_levels, native, levels)
        return quote_from_horizon_quantiles(mapped, hours, levels)

    def _load(self):
        cls = type(self)
        if cls._pipeline is None:
            from chronos import BaseChronosPipeline

            cls._pipeline = BaseChronosPipeline.from_pretrained(
                HF_ID,
                device_map="cpu",
                torch_dtype=torch.float32,
            )
        return cls._pipeline
