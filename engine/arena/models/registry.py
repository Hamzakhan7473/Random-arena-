from __future__ import annotations

from arena.errors import ArenaError
from arena.models.base import Forecaster
from arena.models.baselines import AR1, EWMADrift, NoChange
from arena.models.garch import GarchVol
from arena.models.naive import NaiveLast
from arena.models.random_walk import RandomWalk
from arena.models.torch_quantile import TorchQuantile

MODEL_TYPES = {
    NaiveLast.model_id: NaiveLast,
    RandomWalk.model_id: RandomWalk,
    GarchVol.model_id: GarchVol,
    TorchQuantile.model_id: TorchQuantile,
}

FORECASTER_TYPES: dict[str, type[Forecaster]] = {
    NoChange.name: NoChange,
    EWMADrift.name: EWMADrift,
    AR1.name: AR1,
}


def _register_foundation() -> None:
    try:
        import chronos  # noqa: F401
        from arena.models.chronos2 import Chronos2
        from arena.models.chronos_bolt import ChronosBoltSmall

        FORECASTER_TYPES[ChronosBoltSmall.name] = ChronosBoltSmall
        FORECASTER_TYPES[Chronos2.name] = Chronos2
    except ImportError:
        pass
    try:
        import timesfm  # noqa: F401
        from arena.models.timesfm import TimesFM25

        FORECASTER_TYPES[TimesFM25.name] = TimesFM25
    except ImportError:
        pass


_register_foundation()


def load_models(names: list[str] | None = None) -> list[object]:
    chosen = list(MODEL_TYPES) if names is None else names
    unknown = [name for name in chosen if name not in MODEL_TYPES]
    if unknown:
        known = ", ".join(MODEL_TYPES)
        raise ArenaError(f"unknown model {unknown[0]}; known models: {known}")
    return [MODEL_TYPES[name]() for name in chosen]


def load_forecasters(names: list[str] | None = None) -> list[Forecaster]:
    chosen = list(FORECASTER_TYPES) if names is None else names
    unknown = [name for name in chosen if name not in FORECASTER_TYPES]
    if unknown:
        known = ", ".join(FORECASTER_TYPES)
        raise ArenaError(f"unknown model {unknown[0]}; known models: {known}")
    return [FORECASTER_TYPES[name]() for name in chosen]
