# Python file imports

Finds every module a Python file imports, directly or indirectly, and shows
how Cachify reacts as the files change. It is the full version of the example
in the [main README](../../README.md#a-bigger-example).

Run it from the repository root:

```sh
python examples/py-file-imports/main.py
```

The sample modules are written to `modules/` in this directory, which is
emptied at the start of every run and ignored by git. `db.py` and
`models.py` import each other, so they form a cycle.

The script prints a line each time a cached function actually runs. The
expected output is in [`expected_output.txt`](expected_output.txt). What
happens in each step:

1. **First run:** everything is computed. `db.py` and `models.py` are resolved
   twice: the first pass starts from an empty set for the cycle, and the
   second pass confirms the result no longer changes.
2. **Nothing changed:** everything comes from the cache.
3. **Only a comment changed:** `ui.py` is parsed again, but its imports are the
   same, so nothing that depends on them runs again.
4. **A new import:** only `ui.py` and the modules that depend on it are
   resolved again, plus the new `theme.py`. The `db`/`models` cycle is reused.
