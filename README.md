# Cachify

[![Vibe-Scale 3: Significant AI use with understanding or testing gaps](https://img.shields.io/badge/Vibe--Scale%203-Significant%20AI%20with%20gaps-ffe066)](https://github.com/vibesdk/vibe-scale/blob/main/scale/vibe-3.md)

Cachify is an experimental Python package for running **incremental
computations**. You write regular Python code, but intensive computations can
be cached to provide a performance benefit. When an input to one of your marked
functions changes, only dependencies that require the changed input are
re-computed.

**Where is it useful?**

 - **Build scripts** that need to compute a certain task deterministically and
   only once.
 - **Compilers** are just one big deterministic input/output function and
   ideally suited to use Cachify.
 - **GitHub actions** where the next build step depends solely on the previous one.
 - Maybe you have an idea where this could be useful? Let us know in the issues!

Cachify was inspired by the [Rust Salsa][salsa] library and tries to use the
same algorithms. For more information, see [this article][redgreen].

```py
from cachify import cache

@cache # This is the magic keyword
def is_prime(n: int) -> bool:
    i = 2
    while i * i <= n and n % i:
        i += 1
    return n > 1 and i * i > n

# takes a quarter of a second
print(is_prime(3314192745739))

# instant
print(is_prime(3314192745739))
```

## Reference

### `@cache(maxsize=128, typed=False, *, cycle_initial=None, cycle_fn=None)`

Decorator that caches a function's results and tracks what they depend on:
global variables, files, directories, environment variables, `Input`s and
other cached functions. When one of those changes, the next call recomputes
the result. Works both as `@cache` and as `@cache(...)`.

- `maxsize`: maximum number of cached results, least recently used first
  out. `None` means no limit. Results that are part of a cycle are never
  evicted.
- `typed`: if true, arguments of different types are cached separately,
  so `f(3)` and `f(3.0)` are distinct calls.
- `cycle_initial`: solves cycles by fixpoint iteration instead of raising
  `CycleError`. When a running call is called again, the inner call returns
  `cycle_initial(*args, **kwds)`, and the outer call re-runs until its
  result stops changing.
- `cycle_fn`: called after every run of a cycle as
  `cycle_fn(previous, value, iteration, *args, **kwds)`. Its return value is
  used instead of `value`, for example to force convergence. Requires
  `cycle_initial`.

Arguments must be hashable. Generator and coroutine functions can't be
cached. The decorated function is a `CachedFunction`.

### `CachedFunction[**P, R]`

Protocol for a function decorated with `@cache`. It is called like the
original function, with the same parameter types `P` and return type `R`,
and also works as a method. It has:

- `f.cache_info()`: returns a `CacheInfo`.
- `f.cache_clear()`: removes all cached results and resets the statistics.
- `f.cache_parameters()`: returns a `CacheParameters`.
- `f.__wrapped__`: the original function.

### `CacheInfo`

Named tuple returned by `cache_info()`, with the fields `hits`, `misses`,
`maxsize` and `currsize`.

### `CacheParameters`

Typed dict returned by `cache_parameters()`, with the decorator's arguments
`maxsize`, `typed`, `cycle_initial` and `cycle_fn`.

### `Input[T](value)`

A value that cached functions can depend on, for state Cachify can't track
by itself, such as the contents of a mutable object.

- `get()`: returns the value, of type `T`, and records it as a dependency.
- `set(value)`: changes the value, so results that read it are recomputed.
  Raises `RuntimeError` if called while a cached function is running.

### `CycleError`

Subclass of `RuntimeError`. Raised when a cached function depends on its
own result and has no `cycle_initial`, or when fixpoint iteration doesn't
converge within 200 runs.

### `UntrackedEffectWarning`

Subclass of `RuntimeWarning`. Issued when a cached function uses state that
Cachify doesn't track, such as writing files, starting subprocesses or using
the network. The result won't be recomputed when that state changes. Use the
warning filter `error::cachify.UntrackedEffectWarning` to turn it into an
error.

[salsa]: https://salsa-rs.netlify.app/
[redgreen]: https://rustc-dev-guide.rust-lang.org/queries/incremental-compilation.html
