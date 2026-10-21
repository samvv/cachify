"""The cache decorator and the red-green algorithm."""

from collections.abc import Callable
from functools import _make_key, update_wrapper
from itertools import count
from typing import (Any, Concatenate, NamedTuple, Protocol, Self, TypedDict,
                    cast, overload)

from . import _state
from ._errors import CycleError
from ._globals import start_executing, stop_executing
from ._inputs import poll_inputs



class CacheInfo(NamedTuple):
    """Statistics of a cached function, returned by cache_info()."""

    hits: int
    misses: int
    maxsize: int | None
    currsize: int


class CacheParameters(TypedDict):
    """The arguments of cache(), returned by cache_parameters()."""

    maxsize: int | None
    typed: bool
    cycle_initial: Callable[..., Any] | None
    cycle_fn: Callable[..., Any] | None


class CachedFunction[**P, R](Protocol):
    """A function decorated with cache().

    It is called like the original function, which is available as
    __wrapped__.  Used as a method, it binds like a function.
    """

    __name__: str
    __qualname__: str

    @property
    def __wrapped__(self) -> Callable[P, R]:
        """The original function."""
        ...

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R: ...

    def cache_info(self) -> CacheInfo:
        """Report cache statistics."""
        ...

    def cache_clear(self) -> None:
        """Clear the cache and cache statistics."""
        ...

    def cache_parameters(self) -> CacheParameters:
        """Return the parameters the cache was created with."""
        ...

    @overload
    def __get__(self, instance: None, owner: type, /) -> Self: ...
    @overload
    def __get__[S, **Q](self: 'CachedFunction[Concatenate[S, Q], R]',
                        instance: S, owner: type | None = None, /
                        ) -> 'CachedFunction[Q, R]': ...


class _Decorator(Protocol):
    """What cache() returns when it is given options."""

    def __call__[**P, R](self, user_function: Callable[P, R], /
                         ) -> CachedFunction[P, R]: ...

NO_VALUE = _state.NO_VALUE

# Code flags of functions that cannot be cached
_CO_GENERATOR = 0x20
_CO_COROUTINE = 0x80
_CO_ASYNC_GENERATOR = 0x200

# How many times a cycle head may re-run before giving up, as in Salsa.
MAX_ITERATIONS = 200

# Every run of a cached function gets a new id, so results computed from a
# provisional value can tell whether that value is still current.
_run_ids = count()

# Memos that are being brought up to date and were assumed unchanged when
# reached again, one set per refresh() that is running.
_assumed = []


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
                 'verified_at', 'changed_at', 'in_progress', 'dirty', 'dead',
                 'run_id', 'cycle', 'cycle_heads', 'cycle_of', 'pinned',
                 'verifying', 'pending', 'assumes')

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
        self.run_id = -1            # id of the latest run
        self.cycle: Cycle | None = None  # fixpoint state while it is a cycle head
        # running cycle heads it used -> their run id
        self.cycle_heads: dict[Memo, int] = {}
        # heads of the cycles it was computed in
        self.cycle_of: tuple[Memo, ...] = ()
        self.pinned = False         # part of a cycle, so never evicted
        self.verifying = False      # being brought up to date
        # memos confirmed assuming it is unchanged, with their old verified_at
        self.pending: list[tuple[Memo, int]] | None = None
        # memos it was confirmed assuming unchanged
        self.assumes: set[Memo] | None = None


class CycleChanged(BaseException):
    """A result in the cycle of *head* must re-run while *head* is checked.

    Re-running one result of a cycle on its own would reuse the old values of
    the others, so the check is abandoned and *head* solves the cycle again.
    """

    def __init__(self, head):
        self.head = head


class Cycle:
    """Fixpoint state of a cached call that was re-entered while running."""

    __slots__ = ('value', 'deps', 'hit', 'computing')

    def __init__(self):
        self.value = NO_VALUE       # provisional value handed to inner calls
        self.deps = {}              # what cycle_initial read
        self.hit = False            # re-entered during the current run
        self.computing = False      # cycle_initial is running


def _confirm(memo):
    """*memo* is up to date, so what assumed it unchanged may be too."""
    pending, memo.pending = memo.pending, None
    for other, _ in pending or ():
        assumes = other.assumes
        if assumes is not None:
            assumes.discard(memo)
            if not assumes:
                other.assumes = None
                _confirm(other)


def _open_assumptions(memo):
    """The memos being checked that *memo* was confirmed assuming unchanged.

    A memo whose check is over but which rests on assumptions itself is
    replaced by those assumptions.
    """
    found = set()
    seen = set()
    todo = list(memo.assumes or ())
    while todo:
        other = todo.pop()
        if other in seen:
            continue
        seen.add(other)
        if other.verifying:
            found.add(other)
        elif other.assumes:
            todo.extend(other.assumes)
    return found


def _all_heads(memo):
    """The heads of the cycles *memo* was computed in, including outer ones."""
    heads = []
    todo = list(memo.cycle_of)
    while todo:
        head = todo.pop()
        if head is not memo and head not in heads:
            heads.append(head)
            todo.extend(head.cycle_of)
    return heads


class Query:
    """The memo table of one cached function."""

    def __init__(self, user_function, maxsize, typed, cycle_initial=None,
                 cycle_fn=None):
        self.user_function = user_function
        self.cycle_initial = cycle_initial
        self.cycle_fn = cycle_fn
        self.maxsize = maxsize
        self.typed = typed
        self.memos = {}     # key -> memo
        self.values = {}    # key -> memo holding a value, least recently used first
        self.pinned = {}    # key -> memo in a cycle; these are never evicted
        self.hits = self.misses = 0

    def call(self, args: tuple[Any, ...], kwds: dict[str, Any]) -> Any:
        key = _make_key(args, kwds, self.typed)
        with _state.lock:
            if _state.active.get() is None:
                poll_inputs()
            memo = self.memos.get(key)
            if memo is None:
                memo = self.memos[key] = Memo(self, key, args, kwds)
            value = self.refresh(memo, called=True)
            if value is NO_VALUE:
                value = memo.value
                if value is NO_VALUE:
                    # Up to date, but the value was evicted.  Recomputing it
                    # gives the same result, so it keeps its change revision.
                    value = self.execute(memo, keep_changed_at=True)
                else:
                    self.hits += 1
                    if key in self.values:
                        self.values[key] = self.values.pop(key)
            caller = _state.active.get()
            if caller is not None:
                caller.deps[memo] = None
                # A result that used a provisional value is provisional too.
                if memo.in_progress:
                    caller.cycle_heads[memo] = memo.run_id
                for head, run_id in memo.cycle_heads.items():
                    caller.cycle_heads.setdefault(head, run_id)
            return value

    def refresh(self, memo, called=False):
        """Bring *memo* up to date.

        *called* is true when a function calls *memo*, rather than when the
        dependencies of another memo are checked.  Return the new value if
        the function had to run, else NO_VALUE.
        """
        if memo.in_progress:
            return self.enter_cycle(memo)
        if memo.verifying:
            if called:
                # A function needs the value of *memo* while *memo* is being
                # checked: a new cycle.  Solve it from scratch.
                raise CycleChanged(memo)
            # Checking a solved cycle led back to *memo*.  As in Salsa,
            # assume it is unchanged.  What is confirmed on that assumption
            # is undone if it turns out to be wrong.
            _assumed[-1].add(memo)
            return NO_VALUE
        # A result in a solved cycle is only up to date if its cycle is.
        cycle_of = _all_heads(memo) if memo.cycle_of else ()
        for head in cycle_of:
            if not (head.dead or head.in_progress or head.verifying):
                head.query.refresh(head)
        if memo.cycle_heads and not self.heads_current(memo):
            unchanged = False
        elif memo.dirty:
            unchanged = False
        elif memo.verified_at == _state.revision:
            if memo.assumes and _assumed:
                _assumed[-1].update(_open_assumptions(memo))
            return NO_VALUE
        else:
            unchanged = None
        old_verified_at = memo.verified_at
        assumed = set()
        _assumed.append(assumed)
        memo.verifying = True
        try:
            try:
                if unchanged is None:
                    unchanged = self.deps_unchanged(memo)
                if not unchanged:
                    for head in cycle_of:
                        if head.verifying:
                            raise CycleChanged(head)
            except CycleChanged as e:
                if e.head is not memo:
                    raise
                unchanged = False
            if unchanged:
                memo.verified_at = _state.revision
                value = NO_VALUE
            else:
                self.undo_assumption(memo)
                # The new value doesn't rest on what the check assumed.
                assumed.clear()
                memo.verifying = False
                value = self.execute(memo)
                assumed.discard(memo)
                if assumed:
                    # The new value used values that were only assumed to be
                    # unchanged: a new cycle.  Solve it from scratch.
                    memo.dirty = True
                    raise CycleChanged(next(iter(assumed)))
        except BaseException:
            self.undo_assumption(memo)
            raise
        finally:
            _assumed.pop()
            memo.verifying = False
        assumed.discard(memo)
        if assumed:
            # Only up to date if the memos assumed unchanged are.
            memo.assumes = assumed
            for head in assumed:
                if head.pending is None:
                    head.pending = []
                head.pending.append((memo, old_verified_at))
            _assumed[-1].update(assumed)
        else:
            memo.assumes = None
            _confirm(memo)
        return value

    def undo_assumption(self, memo):
        """*memo* changed after all; undo what was confirmed assuming not."""
        pending, memo.pending = memo.pending, None
        for other, verified_at in pending or ():
            if other.assumes is not None:
                other.assumes = None
                other.verified_at = verified_at
                self.undo_assumption(other)

    def enter_cycle(self, memo):
        """Return the provisional value of *memo*, which is running."""
        name = self.user_function.__qualname__
        if self.cycle_initial is None:
            raise CycleError(f'{name} depends on its own result')
        cycle = memo.cycle
        if cycle is None:
            cycle = memo.cycle = Cycle()
        if cycle.value is NO_VALUE:
            if cycle.computing:
                raise CycleError(f'cycle_initial of {name} depends on '
                                 f'the result of {name}')
            # Charge what cycle_initial reads to the cycle head.
            probe = Memo(self, memo.key, memo.args, memo.kwds)
            cycle.computing = True
            token = _state.active.set(probe)
            try:
                cycle.value = self.cycle_initial(*memo.args, **memo.kwds)
            finally:
                _state.active.reset(token)
                cycle.computing = False
            cycle.deps = probe.deps
            memo.deps.update(probe.deps)
            for head, run_id in probe.cycle_heads.items():
                memo.cycle_heads.setdefault(head, run_id)
        cycle.hit = True
        return cycle.value

    def heads_current(self, memo):
        """Check that the provisional values *memo* used are still current.

        A head that has since converged is replaced by the heads it used
        itself, since its final value is the one *memo* was computed with.
        """
        heads = memo.cycle_heads
        todo = list(heads.items())
        while todo:
            head, run_id = todo.pop()
            if head.dead or head.run_id != run_id:
                return False
            if not head.in_progress:
                if head.dirty:
                    return False
                del heads[head]
                for outer, outer_id in head.cycle_heads.items():
                    if outer is memo:
                        continue
                    known = heads.get(outer)
                    if known is None:
                        heads[outer] = outer_id
                        todo.append((outer, outer_id))
                    elif known != outer_id:
                        return False
        return True

    def deps_unchanged(self, memo):
        for dep in memo.deps:
            if isinstance(dep, Memo):
                if dep.dead:
                    return False
                dep.query.refresh(dep)
                if dep.in_progress or dep.cycle_heads:
                    # Part of a cycle that is still running.  Re-run so the
                    # provisional value is tracked.
                    return False
                changed_at = dep.changed_at
            else:
                changed_at = dep._changed_at
            if changed_at > memo.verified_at:
                return False
        return True

    def execute(self, memo, keep_changed_at=False):
        old_value = memo.value
        memo.in_progress = True
        token = _state.active.set(memo)
        start_executing()
        try:
            iterations = 0
            while True:
                cycle = memo.cycle
                memo.deps = dict(cycle.deps) if cycle else {}
                memo.codes = set()
                memo.cycle_heads = {}
                memo.run_id = next(_run_ids)
                if cycle:
                    cycle.hit = False
                self.misses += 1
                value = self.user_function(*memo.args, **memo.kwds)
                cycle = memo.cycle
                if cycle is None or not cycle.hit:
                    break
                if self.cycle_fn is not None:
                    value = self.cycle_fn(cycle.value, value, iterations,
                                          *memo.args, **memo.kwds)
                # The call was re-entered.  Run again with the new value as
                # the provisional one until it stops changing.
                if _same(cycle.value, value):
                    break
                iterations += 1
                if iterations >= MAX_ITERATIONS:
                    raise CycleError(
                        f'{self.user_function.__qualname__} did not converge '
                        f'after {MAX_ITERATIONS} iterations')
                cycle.value = value
        except BaseException:
            memo.dirty = True
            if memo.changed_at < 0 and not memo.dead:
                # Never computed.  Only results in a cycle that failed with
                # it can refer to it, and they see it is gone.
                memo.dead = True
                del self.memos[memo.key]
            raise
        finally:
            stop_executing()
            _state.active.reset(token)
            memo.in_progress = False
            is_head = memo.cycle is not None and memo.cycle.hit
            memo.cycle = None
        memo.cycle_heads.pop(memo, None)
        memo.cycle_of = tuple(memo.cycle_heads)
        # Re-running one result of a cycle on its own would enter the cycle
        # again, so their values are kept.
        memo.pinned = is_head or bool(memo.cycle_of)
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
        self.pinned.pop(memo.key, None)
        if memo.pinned:
            memo.value = value
            self.pinned[memo.key] = memo
            return
        if self.maxsize == 0:
            memo.value = NO_VALUE
            return
        memo.value = value
        values[memo.key] = memo
        if self.maxsize is not None and len(values) > self.maxsize:
            # Evict the least recently used value, but keep its dependencies
            # so results that depend on it can still be checked.
            values.pop(next(iter(values))).value = NO_VALUE

    def info(self) -> CacheInfo:
        with _state.lock:
            return CacheInfo(self.hits, self.misses, self.maxsize,
                             len(self.values) + len(self.pinned))

    def clear(self) -> None:
        with _state.lock:
            # Results that depend on these memos will be recomputed.
            for memo in self.memos.values():
                memo.dead = True
                memo.value = NO_VALUE
            self.memos.clear()
            self.values.clear()
            self.pinned.clear()
            self.hits = self.misses = 0


@overload
def cache[**P, R](maxsize: Callable[P, R], /) -> CachedFunction[P, R]: ...
@overload
def cache(maxsize: int | None = 128, typed: bool = False, *,
          cycle_initial: None = None, cycle_fn: None = None) -> _Decorator: ...
@overload
def cache[**P, R](maxsize: int | None = 128, typed: bool = False, *,
                  cycle_initial: Callable[P, R],
                  cycle_fn: Callable[Concatenate[R, R, int, P], R] | None = None
                  ) -> Callable[[Callable[P, R]], CachedFunction[P, R]]: ...
def cache(maxsize: Any = 128, typed: bool = False, *,
          cycle_initial: Callable[..., Any] | None = None,
          cycle_fn: Callable[..., Any] | None = None) -> Any:
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

    A cached function that calls itself, directly or through other cached
    functions, raises CycleError.  If *cycle_initial* is given, the cycle is
    solved by fixpoint iteration instead: the inner call returns
    cycle_initial(*args, **kwds), called with the same arguments, and the
    outer call runs again with its own result as the inner value until the
    result stops changing.  CycleError is raised if it doesn't converge
    within MAX_ITERATIONS runs.

    If *cycle_fn* is given as well, it is called after every run of the
    outer call as cycle_fn(previous, value, iteration, *args, **kwds), where
    *previous* is the provisional value the run used, *value* is its result
    and *iteration* counts the earlier runs, starting at 0.  It returns the
    value to use instead of *value*, for example to widen a value that keeps
    growing so the iteration converges.  By default *value* is used as is.

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

    if cycle_initial is not None and not callable(cycle_initial):
        raise TypeError('cycle_initial must be callable')
    if cycle_fn is not None:
        if not callable(cycle_fn):
            raise TypeError('cycle_fn must be callable')
        if cycle_initial is None:
            raise TypeError('cycle_fn requires cycle_initial')

    def decorating_function(user_function: Callable[..., Any]) -> Any:
        return _make_wrapper(user_function, maxsize, typed, cycle_initial,
                             cycle_fn)

    return decorating_function


def _make_wrapper[**P, R](
        user_function: Callable[P, R], maxsize: int | None, typed: bool,
        cycle_initial: Callable[..., Any] | None = None,
        cycle_fn: Callable[..., Any] | None = None) -> CachedFunction[P, R]:
    if not callable(user_function):
        raise TypeError("the first argument must be callable")
    code = getattr(user_function, '__code__', None)
    if code is not None and code.co_flags & (
            _CO_GENERATOR | _CO_COROUTINE | _CO_ASYNC_GENERATOR):
        raise TypeError("generator and coroutine functions cannot be cached")

    query = Query(user_function, maxsize, typed, cycle_initial, cycle_fn)

    def wrapper(*args: P.args, **kwds: P.kwargs) -> R:
        return query.call(args, kwds)

    def cache_info() -> CacheInfo:
        """Report cache statistics"""
        return query.info()

    def cache_clear() -> None:
        """Clear the cache and cache statistics"""
        query.clear()

    def cache_parameters() -> CacheParameters:
        """Return the parameters the cache was created with"""
        return {'maxsize': maxsize, 'typed': typed,
                'cycle_initial': cycle_initial, 'cycle_fn': cycle_fn}

    for helper in (cache_info, cache_clear, cache_parameters):
        setattr(wrapper, helper.__name__, helper)
    # A plain function with attributes, so it binds as a method.
    return cast(CachedFunction[P, R], update_wrapper(wrapper, user_function))
