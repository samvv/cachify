"""The cache decorator and the red-green algorithm."""

from collections import namedtuple
from functools import _make_key, update_wrapper

from . import _state
from ._errors import CycleError
from ._globals import start_executing, stop_executing
from ._inputs import poll_inputs

CacheInfo = namedtuple("CacheInfo", ("hits", "misses", "maxsize", "currsize"))

NO_VALUE = _state.NO_VALUE

# Code flags of functions that cannot be cached
_CO_GENERATOR = 0x20
_CO_COROUTINE = 0x80
_CO_ASYNC_GENERATOR = 0x200


def _same(a, b):
    if a is b:
        return True
    try:
        return bool(a == b)
    except Exception:
        return False


class Memo:
    """The cached result of one call, with its dependencies."""

    __slots__ = ('query', 'key', 'args', 'kwds', 'value', 'deps', 'codes',
                 'verified_at', 'changed_at', 'in_progress', 'dirty', 'dead')

    def __init__(self, query, key, args, kwds):
        self.query = query
        self.key = key
        self.args = args
        self.kwds = kwds
        self.value = NO_VALUE       # dropped when evicted
        self.deps = {}              # inputs and memos read, in order
        self.codes = set()          # code objects that ran
        self.verified_at = -1       # last revision confirmed up to date
        self.changed_at = -1        # last revision the value changed in
        self.in_progress = False    # running right now
        self.dirty = True           # must be recomputed
        self.dead = False           # removed by cache_clear()


class Query:
    """The memo table of one cached function."""

    def __init__(self, user_function, maxsize, typed):
        self.user_function = user_function
        self.maxsize = maxsize
        self.typed = typed
        self.memos = {}     # key -> memo
        self.values = {}    # key -> memo holding a value, least recently used first
        self.hits = self.misses = 0

    def call(self, args, kwds):
        key = _make_key(args, kwds, self.typed)
        with _state.lock:
            if _state.active.get() is None:
                poll_inputs()
            memo = self.memos.get(key)
            if memo is None:
                memo = self.memos[key] = Memo(self, key, args, kwds)
            value = self.refresh(memo)
            if value is NO_VALUE:
                value = memo.value
                if value is NO_VALUE:
                    # Up to date, but the value was evicted.  Recomputing it
                    # gives the same result, so it keeps its change revision.
                    value = self.execute(memo, keep_changed_at=True)
                else:
                    self.hits += 1
                    self.values[key] = self.values.pop(key)
            _state.record(memo)
            return value

    def refresh(self, memo):
        """Bring *memo* up to date.

        Return the new value if the function had to run, else NO_VALUE.
        """
        if memo.in_progress:
            raise CycleError(
                f'{self.user_function.__qualname__} depends on its own result')
        if not memo.dirty:
            if memo.verified_at == _state.revision:
                return NO_VALUE
            if self.deps_unchanged(memo):
                memo.verified_at = _state.revision
                return NO_VALUE
        return self.execute(memo)

    def deps_unchanged(self, memo):
        for dep in memo.deps:
            if isinstance(dep, Memo):
                if dep.dead:
                    return False
                dep.query.refresh(dep)
                changed_at = dep.changed_at
            else:
                changed_at = dep._changed_at
            if changed_at > memo.verified_at:
                return False
        return True

    def execute(self, memo, keep_changed_at=False):
        old_value = memo.value
        memo.deps = {}
        memo.codes = set()
        memo.in_progress = True
        self.misses += 1
        token = _state.active.set(memo)
        start_executing()
        try:
            value = self.user_function(*memo.args, **memo.kwds)
        except BaseException:
            memo.dirty = True
            if memo.changed_at < 0 and not memo.dead:
                # Never computed, so nothing depends on it yet.
                del self.memos[memo.key]
            raise
        finally:
            stop_executing()
            _state.active.reset(token)
            memo.in_progress = False
        if not keep_changed_at and not (
                old_value is not NO_VALUE and _same(old_value, value)):
            memo.changed_at = _state.revision
        memo.verified_at = _state.revision
        memo.dirty = False
        self.store(memo, value)
        return value

    def store(self, memo, value):
        if memo.dead:
            return
        values = self.values
        values.pop(memo.key, None)
        if self.maxsize == 0:
            memo.value = NO_VALUE
            return
        memo.value = value
        values[memo.key] = memo
        if self.maxsize is not None and len(values) > self.maxsize:
            # Evict the least recently used value, but keep its dependencies
            # so results that depend on it can still be checked.
            values.pop(next(iter(values))).value = NO_VALUE

    def info(self):
        with _state.lock:
            return CacheInfo(self.hits, self.misses, self.maxsize,
                             len(self.values))

    def clear(self):
        with _state.lock:
            # Results that depend on these memos will be recomputed.
            for memo in self.memos.values():
                memo.dead = True
                memo.value = NO_VALUE
            self.memos.clear()
            self.values.clear()
            self.hits = self.misses = 0


def cache(maxsize=128, typed=False):
    """Cache the results of a function and track what they depend on.

    While the function runs, the global variables, files and environment
    variables it reads, every Input it reads, and every other cached
    function it calls are recorded.  When one of those changes, the next
    call recomputes the result; otherwise the cached result is returned.

    If *maxsize* is set to None, the cache can grow without bound.  Otherwise
    the least recently used results are discarded once there are more than
    *maxsize* of them.

    If *typed* is True, arguments of different types will be cached
    separately.  For example, f(decimal.Decimal("3.0")) and f(3.0) will be
    treated as distinct calls with distinct results.  Some types such as
    str and int may be cached separately even when typed is false.

    Arguments to the cached function must be hashable.  Generator and
    coroutine functions cannot be cached.

    The decorator can be used both as @cache() and as @cache.

    View the cache statistics named tuple (hits, misses, maxsize, currsize)
    with f.cache_info().  Clear the cache and statistics with
    f.cache_clear().  Access the underlying function with f.__wrapped__.
    """

    if isinstance(maxsize, int):
        # Negative maxsize is treated as 0
        if maxsize < 0:
            maxsize = 0

    elif callable(maxsize) and isinstance(typed, bool):
        # Used as @cache without parentheses
        user_function, maxsize = maxsize, 128
        return _make_wrapper(user_function, maxsize, typed)

    elif maxsize is not None:
        raise TypeError(
            'Expected first argument to be an integer, a callable, or None')

    def decorating_function(user_function):
        return _make_wrapper(user_function, maxsize, typed)

    return decorating_function


def _make_wrapper(user_function, maxsize, typed):
    if not callable(user_function):
        raise TypeError("the first argument must be callable")
    code = getattr(user_function, '__code__', None)
    if code is not None and code.co_flags & (
            _CO_GENERATOR | _CO_COROUTINE | _CO_ASYNC_GENERATOR):
        raise TypeError("generator and coroutine functions cannot be cached")

    query = Query(user_function, maxsize, typed)

    def wrapper(*args, **kwds):
        return query.call(args, kwds)

    def cache_info():
        """Report cache statistics"""
        return query.info()

    def cache_clear():
        """Clear the cache and cache statistics"""
        query.clear()

    def cache_parameters():
        """Return the parameters the cache was created with"""
        return {'maxsize': maxsize, 'typed': typed}

    wrapper.cache_info = cache_info # type: ignore
    wrapper.cache_clear = cache_clear # type: ignore
    wrapper.cache_parameters = cache_parameters # type: ignore
    return update_wrapper(wrapper, user_function)
