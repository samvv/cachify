import linecache
import os
import sys
import warnings

import pytest

import cachify
from helpers import assert_no_untracked, assert_warns_untracked


@pytest.fixture


def module_dir(tmp_path, monkeypatch):
    """A directory on sys.path; modules imported from it are unloaded after."""
    monkeypatch.syspath_prepend(str(tmp_path))
    before = set(sys.modules)
    yield tmp_path
    for name in set(sys.modules) - before:
        file = getattr(sys.modules[name], '__file__', None) or ''
        if file.startswith(str(tmp_path)):
            del sys.modules[name]


def test_open_for_writing():
    @cachify.cache()
    def read():
        with open(os.devnull, 'w') as f:
            return f.write('x')

    w = assert_warns_untracked(read, 'read()', "'open'", repr(os.devnull))
    # The warning points at the code that opened the file
    assert w.filename == __file__


def test_not_cached_no_warning():
    def read():
        with open(os.devnull, 'w') as f:
            return f.write('x')

    assert_no_untracked(read)


def test_cache_hit_no_warning():
    @cachify.cache()
    def read():
        with open(os.devnull, 'w') as f:
            return f.write('x')

    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        read()
    assert_no_untracked(read)


def test_uncached_helper():
    def helper():
        return open(os.devnull, 'w').close()

    @cachify.cache()
    def outer():
        return helper()

    assert_warns_untracked(outer, 'outer()', "'open'")


def test_innermost_cached_function_is_named():
    @cachify.cache()
    def inner():
        return open(os.devnull, 'w').close()

    @cachify.cache()
    def outer():
        return inner()

    assert_warns_untracked(outer, 'inner()')


def test_environment_change():
    @cachify.cache()
    def set_env():
        os.environ['CACHIFY_TEST_VAR'] = '1'

    try:
        assert_warns_untracked(set_env, "'os.putenv'")
    finally:
        os.environ.pop('CACHIFY_TEST_VAR', None)


def test_pure_function_no_warning():
    @cachify.cache()
    def add(a, b):
        return a + b

    assert_no_untracked(lambda: add(1, 2))


def test_import_no_warning(module_dir):
    (module_dir / 'cachify_test_mod.py').write_text('VALUE = 42\n')

    @cachify.cache()
    def load():
        import cachify_test_mod
        return cachify_test_mod.VALUE

    assert_no_untracked(load)


def test_warning_as_error():
    calls = []

    @cachify.cache()
    def read():
        calls.append(1)
        with open(os.devnull, 'w'):
            pass

    with warnings.catch_warnings():
        warnings.simplefilter('error', cachify.UntrackedEffectWarning)
        for _ in range(2):
            with pytest.raises(cachify.UntrackedEffectWarning):
                read()
    # The failed call is not cached
    assert len(calls) == 2


def test_showing_warning_does_not_recurse():
    # Showing a warning can itself have untracked effects, such as
    # writing to a log file, which must not issue another warning.
    @cachify.cache()
    def read():
        with open(os.devnull, 'w'):
            pass

    shown = []

    def showwarning(message, category, filename, lineno, file=None,
                    line=None):
        with open(os.devnull, 'a') as log:
            log.write(warnings.formatwarning(message, category, filename,
                                             lineno, line))
        shown.append(message)

    with warnings.catch_warnings():
        warnings.simplefilter('always')
        warnings.showwarning = showwarning
        read()
    assert len(shown) == 1


def test_showing_warning_adds_no_dependency(module_dir):
    # Formatting a warning reads the source line from disk.  That file
    # must not become a dependency of the cached call.
    path = module_dir / 'cachify_warn_mod.py'
    path.write_text('import os, cachify\n'
                    'calls = []\n'
                    '@cachify.cache()\n'
                    'def write():\n'
                    '    calls.append(1)\n'
                    '    open(os.devnull, "w").close()\n')
    import cachify_warn_mod as mod

    def showwarning(message, category, filename, lineno, file=None,
                    line=None):
        warnings.formatwarning(message, category, filename, lineno, line)

    linecache.clearcache()
    with warnings.catch_warnings():
        warnings.simplefilter('always')
        warnings.showwarning = showwarning
        mod.write()
        with open(path, 'a') as f:
            f.write('# changed\n')
        mod.write()
    assert len(mod.calls) == 1
