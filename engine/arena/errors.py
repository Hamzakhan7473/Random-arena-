class ArenaError(Exception):
    """An operational failure with a message safe to print."""


class PublishedTooLate(ArenaError):
    pass


class ForecastImmutable(ArenaError):
    pass


class ManifestError(ArenaError):
    pass


class ForecastFormat(ArenaError):
    pass


class InsufficientHistory(ArenaError):
    pass


class ActualConflict(ArenaError):
    pass


class IngestError(ArenaError):
    pass


class BackfillCutoff(ArenaError):
    pass
