"""Incremental computation with dependency tracking.

Functions decorated with :func:`cache` are memoized.  While a cached function
runs, everything it reads and every other cached function it calls is
recorded as a dependency.  When something it read changes, the next call
recomputes the result; otherwise the cached result is returned.  Functions
without the decorator run as usual; anything they read is charged to the
nearest cached call that is still running.

Cachify tracks these reads by itself:

* global variables and builtins, including the functions that are called;
* files opened for reading, and directories listed with os.listdir() or
  os.scandir();
* environment variables read through os.environ or os.getenv().

They are checked for changes at the start of every outermost call to a cached
function.  State that cachify cannot see, such as the contents of mutable
objects, can be wrapped in an :class:`Input`.

This follows the red-green algorithm of the Rust Salsa library:

* A global revision counter goes up every time an input changes.
* Every cached result (a "memo") remembers the revision in which it was last
  confirmed up to date and the revision in which its value last changed.
* A memo is up to date if none of its dependencies changed after it was last
  confirmed.  Dependencies that are themselves memos are brought up to date
  first.
* When a memo is recomputed and the new value equals the old one, it keeps
  its old change revision, so the results that depend on it don't need to be
  recomputed ("early cutoff").

Cached functions should be pure: their result must depend only on their
arguments and on state that cachify tracks.  When a cached function uses
outside state that cachify does not track, such as writing files, starting
subprocesses or using the network, an UntrackedEffectWarning is issued.  Turn
it into an error with the warnings filter
"error::cachify.UntrackedEffectWarning" to enforce purity.
"""

from ._errors import CycleError, UntrackedEffectWarning
from ._inputs import Input
from ._cache import CachedFunction, CacheInfo, CacheParameters, cache

# Imported for their side effects: they install the hooks that track files
# and environment variables.
from . import _audit, _environ

__all__ = ['cache', 'CachedFunction', 'CacheInfo', 'CacheParameters',
           'CycleError', 'Input', 'UntrackedEffectWarning']
