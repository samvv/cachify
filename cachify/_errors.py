class CycleError(RuntimeError):
    """Raised when a cached function depends on its own result."""


class UntrackedEffectWarning(RuntimeWarning):
    """Issued when a cached function uses state that cachify does not track.

    The cached result will not be recomputed when that state changes, and
    side effects are skipped whenever the cached result is reused.
    """
