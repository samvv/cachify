"""State shared by all cached functions."""
from typing import TYPE_CHECKING

import os
from contextvars import ContextVar
from threading import RLock

if TYPE_CHECKING:
    from cachify._cache import Memo

# A single lock keeps the dependency graph consistent.  It is held while
# cached functions run.
lock = RLock()

# Goes up by one every time an input changes.
revision: int = 0

# The memo of the innermost cached call that is running.
active = ContextVar['Memo | None']('cachify_active', default=None)

# Sentinels
NO_VALUE = object()
MISSING = object()

# Code in this package is not tracked and is skipped in warnings.  Code
# objects keep the path the package was imported from, which may contain '..'
# or a symlink, so the normalized path alone would not match.
PACKAGE_DIRS = tuple(dict.fromkeys(
    os.path.dirname(path) + os.sep
    for path in (__file__, os.path.abspath(__file__),
                 os.path.realpath(__file__))))


def record(dep):
    """Record *dep* as a dependency of the cached call that is running."""
    memo = active.get()
    if memo is not None:
        memo.deps[dep] = None
