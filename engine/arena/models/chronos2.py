"""Chronos-2 adapter.

Checkpoint: ``amazon/chronos-2`` via ``chronos-forecasting>=2.0``.

Official usage (model card + ``Chronos2Pipeline.predict``):

    from chronos import Chronos2Pipeline
    pipeline = Chronos2Pipeline.from_pretrained("amazon/chronos-2", device_map="cpu")
    preds = pipeline.predict(inputs, prediction_length=24)
    # list of tensors, each (n_variates, n_quantiles, prediction_length)

A 1-d context is univariate, so ``preds[0]`` has shape ``(1, n_quantiles, 24)``.

Native training quantiles (checkpoint ``chronos_config.quantiles``) are
``0.01, 0.05, 0.1, ..., 0.9, 0.95, 0.99`` and already include every arena
level ``[0.1, 0.25, 0.5, 0.75, 0.9]``. No interpolation.

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

HF_ID = "amazon/chronos-2"


class Chronos2(Forecaster):
    name = "chronos-2"
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
            # List input of 1-d series is the documented univariate form.
            preds = pipeline.predict(
                [context_tensor],
                prediction_length=PREDICTION_LENGTH,
            )
        # (n_variates, n_quantiles, H) -> (H, n_quantiles)
        native = preds[0][0].detach().cpu().numpy().T
        mapped = interpolate_quantiles(native_levels, native, levels)
        return quote_from_horizon_quantiles(mapped, hours, levels)

    def _load(self):
        cls = type(self)
        if cls._pipeline is None:
            from chronos import Chronos2Pipeline

            cls._pipeline = Chronos2Pipeline.from_pretrained(
                HF_ID,
                device_map="cpu",
                torch_dtype=torch.float32,
            )
        return cls._pipeline
