# Cachify

[![Vibe-Scale 3: Significant AI use with understanding or testing gaps](https://img.shields.io/badge/Vibe--Scale%203-Significant%20AI%20with%20gaps-ffe066)](https://github.com/vibesdk/vibe-scale/blob/main/scale/vibe-3.md)

Cachify is an experimental programming language for running **incremental
computations**. You write regular Python code, but intensive computations can
be cached to provide a performance benefit. When an input to one of your
marked functions changes, only dependencies that require the changed input are
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

[salsa]: https://salsa-rs.netlify.app/
[redgreen]: https://rustc-dev-guide.rust-lang.org/queries/incremental-compilation.html
