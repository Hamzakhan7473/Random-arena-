"""TimesFM 2.5 adapter.

Checkpoint: ``google/timesfm-2.5-200m-pytorch`` via Google's ``timesfm`` package.
The Hugging Face collection id ``google/timesfm-2.5-200m`` is the same 200M
checkpoint family; the torch class loads the ``-pytorch`` weights
(``TimesFM_2p5_200M_torch.DEFAULT_REPO_ID``).

Official usage (model card):

    import timesfm
    model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
        "google/timesfm-2.5-200m-pytorch", torch_compile=False,
    )
    model.compile(timesfm.ForecastConfig(max_context=512, max_horizon=128, ...))
    point, quantile = model.forecast(horizon=24, inputs=[context])
    # point (N, H); quantile (N, H, 10): mean, then 10th–90th quantiles

Native quantile head (``TimesFM_2p5_200M_Definition.quantiles``) is
``[0.1, 0.2, ..., 0.9]``. The 10-wide forecast tensor is mean then those nine
levels; the package has no API to request 0.25 or 0.75. Arena levels 0.1, 0.5,
0.9 are native. 0.25 and 0.75 are linear interpolations in quantile-level
space between 0.2/0.3 and 0.7/0.8.

``max_horizon`` is compiled at 128 (TimesFM 2.5 output patch size); we still
call ``forecast(horizon=24)`` once and read off steps 1, 4, 24.
``infer_is_positive`` is off because log prices may be negative.
``torch_compile`` is off so the job stays on CPU.

Weights live in the Hugging Face Hub cache; the compiled model is a process
singleton.
"""

from __future__ import annotations

import pandas as pd
import torch

from arena.models.base import Forecaster
from arena.models.foundation import (
    CONTEXT_HOURS,
    PREDICTION_LENGTH,
    interpolate_quantiles,
    prepare_forecast,
    quote_from_horizon_quantiles,
)

HF_ID = "google/timesfm-2.5-200m-pytorch"
# Output patch length of TimesFM 2.5; compile() rounds max_horizon up to this.
_COMPILE_HORIZON = 128
_QUANTILE_MEAN_INDEX = 0


class TimesFM25(Forecaster):
    name = "timesfm-2.5"
    version = HF_ID
    _model = None

    def forecast(
        self,
        history: pd.Series,
        horizons: list[int],
        quantiles: list[float],
    ) -> dict[int, dict[float, float]]:
        context, hours, levels = prepare_forecast(history, horizons, quantiles)
        model = self._load()
        native_levels = [float(q) for q in model.model.config.quantiles]
        _point, quantile_forecast = model.forecast(
            horizon=PREDICTION_LENGTH,
            inputs=[context],
        )
        # (1, H, 10) -> skip mean at index 0, keep 0.1..0.9
        native = quantile_forecast[0, :, _QUANTILE_MEAN_INDEX + 1 :]
        mapped = interpolate_quantiles(native_levels, native, levels)
        return quote_from_horizon_quantiles(mapped, hours, levels)

    def _load(self):
        cls = type(self)
        if cls._model is None:
            import timesfm

            model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
                HF_ID,
                torch_compile=False,
            )
            inner = model.model
            inner.device = torch.device("cpu")
            inner.device_count = 1
            inner.to("cpu")
            model.compile(
                timesfm.ForecastConfig(
                    max_context=CONTEXT_HOURS,
                    max_horizon=_COMPILE_HORIZON,
                    normalize_inputs=True,
                    use_continuous_quantile_head=True,
                    force_flip_invariance=True,
                    infer_is_positive=False,
                    fix_quantile_crossing=True,
                )
            )
            cls._model = model
        return cls._model
