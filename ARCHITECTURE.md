# Cachify architecture

Cachify adds Salsa-style incremental computation to Python. Functions marked
with `@cachify.cache()` are memoized, and Cachify records what each call read.
When an input changes, only the cached calls that depend on it are re-run.

Functions without `@cache` run as usual. Anything an unmarked function reads
is charged to the nearest cached call that is still running. Outside cached
calls they run with no hook and no overhead.

Cachify is plain Python and needs no changes to the interpreter. It uses
three hooks that CPython already provides: audit hooks, `sys.monitoring`
and a subclass of `os._Environ`. A C extension could later speed up the hot
paths.

## Layout

| Module | Contents |
| --- | --- |
| `cachify/__init__.py` | Public API: `cache`, `CachedFunction`, `CacheInfo`, `CacheParameters`, `Input`, `CycleError`, `UntrackedEffectWarning` |
| `cachify/_state.py` | Shared state: the lock, the revision counter and the stack of running cached calls |
| `cachify/_inputs.py` | `Input`, and the inputs Cachify creates and polls itself |
| `cachify/_cache.py` | The `cache` decorator, memos and the red-green algorithm |
| `cachify/_globals.py` | Tracking of global variables with `sys.monitoring` |
| `cachify/_environ.py` | Tracking of environment variables |
| `cachify/_audit.py` | The audit hook: file and directory inputs, and warnings |
| `cachify/_errors.py` | `CycleError` and `UntrackedEffectWarning` |

Importing `cachify` installs the hooks. Tests are in `tests/` and run with
`python -m pytest tests`.

## Core algorithm

The core is pure Python and follows Salsa's red-green algorithm.

### State

- **Revision counter.** A global integer that goes up by one every time an
  input changes.
- **Inputs.** State from outside the computation. Each one stores the
  revision it last changed in (`changed_at`). Unlike in Salsa, most inputs
  are created automatically for the global variables, files and environment
  variables a cached call reads (see below). An explicit `cachify.Input` is
  only needed for state Cachify can't see.
- **Memos.** One per `(function, arguments)` pair. Each stores:
  - `value`: the result of the call;
  - `deps`: the inputs and memos read while computing it;
  - `verified_at`: the last revision in which it was confirmed up to date;
  - `changed_at`: the last revision in which its value changed.
- **Active stack.** The cached calls currently running, kept in a
  `contextvars.ContextVar` so it works with threads and asyncio. Every read is
  recorded against the top of this stack.

### Calling a cached function

1. Build a key from the arguments, the same way `functools.lru_cache` does.
2. Look up the memo for that key.
3. If there is no memo, or it fails validation (see below), run the function
   with a new memo pushed onto the active stack.
4. Record the memo as a dependency of the caller, if the caller is itself a
   cached call.
5. Return the memo's value.

### Validation

A memo is up to date if any of these hold:

- `verified_at` equals the current revision;
- or, for each of its dependencies:
  - if the dependency is a memo, it is first brought up to date itself
    (validated, or re-run if stale);
  - and no dependency has `changed_at` greater than this memo's
    `verified_at`.

A memo that passes is stamped with `verified_at = current revision`.
Otherwise it is re-run.

### Early cutoff

When a memo is re-run and the new value equals the old one, it keeps its old
`changed_at`. Callers that depend on it then pass validation without
re-running. Salsa calls this backdating.

### Sketch

```python
_revision = 0
_active = contextvars.ContextVar('cachify_active', default=None)

def _record(dep):
    if (m := _active.get()) is not None:
        m.deps.append(dep)

class Input:
    def __init__(self, value):
        self._value, self.changed_at = value, _revision
    def get(self):
        _record(self)
        return self._value
    def set(self, value):
        global _revision
        _revision += 1
        self._value, self.changed_at = value, _revision

class Query:                                   # what @cache returns
    def __call__(self, *args, **kw):
        key = _make_key(args, kw, self.typed)
        memo = self.memos.get(key)
        if memo is None or not self._validate(memo):
            memo = self._execute(key, args, kw, old=memo)
        _record(memo)                          # caller depends on this memo
        return memo.value

    def _validate(self, memo):                 # the red-green check
        if memo.verified_at == _revision:
            return True
        for dep in memo.deps:
            if isinstance(dep, Memo):
                dep.query._refresh(dep)        # validate, or re-run if stale
            if dep.changed_at > memo.verified_at:
                return False
        memo.verified_at = _revision
        return True

    def _execute(self, key, args, kw, old):
        new = Memo(self, args, kw)             # in_progress=True, for cycle detection
        token = _active.set(new)
        try:
            new.value = self.fn(*args, **kw)
        finally:
            _active.reset(token)
        # Early cutoff: if the value didn't change, callers don't need to re-run.
        new.changed_at = old.changed_at if old and old.value == new.value else _revision
        new.verified_at = _revision
        self.memos[key] = new
        return new
```

### Rules and edge cases

- **Cycles.** If a memo that is still being computed is called again, raise
  `CycleError`, unless its function has a `cycle_initial` (see
  [Cycles](#cycles)).
- **Exceptions.** If the function raises, no memo is stored.
- **Generators and async functions.** Rejected when `@cache` is applied,
  because the decorator can't tell when they finish.
- **`maxsize`.** Results that are part of a cycle are never evicted.
  Eviction drops a memo's value but keeps its dependency
  record, as in Salsa's LRU. A later call can still be checked as up to date,
  and the call only re-runs if the value is needed. Re-running an evicted
  memo that is up to date keeps its `changed_at`, since a pure function gives
  the same result. Dependency records are never evicted.
- **`cache_clear()`.** Removes a function's memos entirely. Cached results
  that depended on them are re-run the next time they are checked.
- **Memo identity.** Re-running a memo updates it in place, so the
  dependency lists of other memos keep pointing at the current one.
- **Inputs during a run.** Calling `Input.set()` while a cached function is
  running raises `RuntimeError`.
- **Threads.** One global lock protects the dependency graph and is held
  while cached functions run, so cached computations run one at a time.
- **Mutable values.** A caller that changes a cached return value in place
  corrupts the cache. Return values should be frozen, or this rule must be
  documented.

### Cycles

`@cache(cycle_initial=fn)` solves a cycle by fixpoint iteration, as in
Salsa:

1. When a running memo is called again, it becomes a **cycle head**. The
   inner call returns a provisional value, `fn(*args, **kwds)`, computed
   once per run of the head with the same arguments.
2. When the head's function returns, its result is compared with the
   provisional value. If they differ, the result becomes the new provisional
   value and the head runs again. Otherwise the cycle has converged. After
   `MAX_ITERATIONS` (200) runs, `CycleError` is raised.

   With `@cache(cycle_initial=..., cycle_fn=fn)`, the result is first passed
   through `fn(previous, value, iteration, *args, **kwds)`, after every run
   including the last, and what it returns is used instead. This lets a
   cycle that would never converge be forced to, for example by widening a
   value that keeps growing. As in Salsa, `cycle_fn` is optional, defaults
   to returning `value`, and requires `cycle_initial`.
3. Results computed from a provisional value are **provisional** too. Each
   one stores the heads it used and the id of the head's run
   (`cycle_heads`). In the next run of the head the ids differ, so they are
   recomputed. Once the head converges, its last run is final, and so are
   the results computed in it.

The call that is re-entered must have `cycle_initial`. The first call into a
cycle is the one that gets re-entered, so it depends on where the cycle is
entered from.

A solved cycle leaves a loop in the dependency graph, which validation has
to handle:

- **Checking a cycle.** When checking a memo's dependencies leads back to
  the memo itself, it is assumed unchanged, as in Salsa. Memos confirmed on
  that assumption are recorded (`pending` and `assumes`). If the memo turns
  out to be unchanged they are confirmed; otherwise they are reset to
  unverified.
- **Re-solving.** A result in a cycle never re-runs on its own while its
  head is checked, because it would use the old values of the rest of the
  cycle and could keep a stale value alive. Instead the check is abandoned
  (`CycleChanged`) and the head runs again from `cycle_initial`. The same
  happens when a result that re-runs during a check uses a value that was
  only assumed unchanged, since that is a new cycle.
- **Entering from the side.** Checking a result in a cycle (`cycle_of`)
  first brings its heads up to date.

## Tracking reads

The algorithm is only correct if every read of changing state is recorded.
That is the hard part in Python, and it is handled in layers.

### Automatic inputs

Cachify creates inputs by itself for three kinds of state. Each automatic
input stores a snapshot of what it watches:

| State | How reads are seen | Snapshot |
| --- | --- | --- |
| Global variables and builtins | `sys.monitoring` `PY_START` events | identity of the value |
| Files and directories | audit hook: `open` (read-only), `os.listdir`, `os.scandir` | inode, size, mtime and ctime of the path |
| Environment variables | `os.environ` given a tracking subclass | the variable's value, or the whole environment for `len()` and iteration |

Nothing reports changes to this state, so it is **polled**. At the start of
every outermost cached call, Cachify re-reads every snapshot. If any differ,
it starts a new revision and stamps the changed inputs with it. The normal
validation then takes over.

#### Global variables

`sys.monitoring` events are only turned on while at least one cached function
is running (`start_executing` / `stop_executing` in `_globals.py`). For each code object that
starts during a cached call, Cachify:

1. reads the `LOAD_GLOBAL` names out of its bytecode with `dis` (cached per
   code object);
2. records one input per name, keyed by the frame's globals dict and the name.

Inputs compare by identity, so rebinding a name invalidates the result and
mutating the object it refers to doesn't. A name with no global value falls
back to the builtins, so shadowing a builtin is noticed too. Functions are
globals, so redefining a helper invalidates everything that called it.

This over-approximates: a name in a branch that never ran still counts. That
causes extra re-runs but never misses a change. Code in the `cachify` package and
the import system is skipped.

#### Files and directories

The audit hook records a file opened without write flags, and a directory
listed with `os.listdir` or `os.scandir`, keyed by absolute path. A missing
file is recorded as missing, so creating it later is noticed. Adding or
removing a directory entry changes the directory's mtime, so `os.walk` and
`pathlib.Path.walk` are covered through `os.scandir`.

#### Environment variables

`os.environ` raises no audit event when read. Instead, its class is swapped
for a subclass of `os._Environ` that records `__getitem__` (and therefore
`get`, `in` and `os.getenv`), `__iter__` and `__len__`. The object keeps its
identity, so `from os import environ` still works.

#### Warnings for everything else

Other effects raise an `UntrackedEffectWarning`. These are writing files,
deleting or renaming them, globbing, subprocesses, the network, `input()`,
and changing the environment. Globbing warns because `glob` checks literal
path components with `lstat`, which raises no audit event.

The warning names the innermost cached call and points at the line that
caused the event. The hook:

- ignores files opened by the import system;
- doesn't trigger itself while a warning is being shown, since that can open
  source files;
- binds its globals as defaults so it keeps working during interpreter
  shutdown.

Running with `-W error::cachify.UntrackedEffectWarning` turns the warnings
into errors, so the operation fails and nothing is cached.

#### Limitations

- **Mutable objects.** In-place changes (`items.append(x)`, `obj.n += 1`),
  attributes of other modules (`config.DEBUG`), and closure variables
  changed with `nonlocal` are not seen. Use `Input` for these.
- **Polling cost.** Every outermost call re-reads every snapshot. A file
  costs about 1.5 µs (one `stat`). Snapshots are never removed.
- **Overhead inside cached calls.** Every Python function started in a cached
  call runs a callback, the first time per cached call.
- **Other tools.** Cachify takes `sys.monitoring` tool ID 4, or 3 if 4 is
  taken. If neither is free, it warns and doesn't track global variables.
- **Process-wide cost.** Audit hooks can't be removed, so after the import
  every audit event in the process costs one dict lookup.

A C extension could replace polling for globals with dict watchers
(`PyDict_AddWatcher`), with no interpreter changes.

## Future work

### Saving the cache between runs

For build scripts and CI, the memo table is saved to disk (pickle or sqlite).
Then a change to the code is also an input: if an unmarked helper changes,
every cached result that ran it is stale.

Each memo already records the code objects that ran while it was computed
(`memo.codes`, filled in by the `sys.monitoring` callback). Saving the cache
means storing a hash of that set (or of the source files it touched) with the
memo, and treating a mismatch as a change. Within one run, redefining a
function is already caught, because functions are global variables.

A frame-entry hook in a CPython fork could record the same set more cheaply,
with one branch per frame instead of a callback, but it isn't required.

### Speed: C hot paths

The common case, a memo already verified in the current revision, can be
moved into a C extension module. Give marked functions their own vectorcall
with `PyFunction_SetVectorcall`, which is public API. CPython's evaluator only
runs a call in place when `func->vectorcall == _PyFunction_Vectorcall`
(`_DO_CALL` in `Python/bytecodes.c`, and `specialize_py_call` in
`Python/specialize.c`). Every call to a marked function therefore reaches the
hook, and unmarked functions are unaffected.

## Roadmap

1. Pure-Python core with explicit `Input`s and tests. **Done.**
2. Audit hooks for catching untracked I/O, plus file and environment inputs.
   **Done**, along with automatic inputs for global variables.
3. Saving the cache to disk, with code fingerprints from `memo.codes`.
4. A C extension for the hot paths and for dict watchers.
