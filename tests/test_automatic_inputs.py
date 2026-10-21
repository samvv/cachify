import os
import subprocess
import sys
from typing import Any

import pytest

import cachify
import cachify._globals
from helpers import assert_no_untracked


@pytest.fixture


def tmp(tmp_path):
    """A temporary directory, as a string path."""
    return str(tmp_path)


@pytest.fixture


def unset_env():
    """Unset environment variables now and again after the test."""
    names = []

    def unset(name):
        names.append(name)
        os.environ.pop(name, None)
        return name

    yield unset
    for name in names:
        os.environ.pop(name, None)


def write(path, text):
    with open(path, 'w') as f:
        f.write(text)


def namespace(source) -> dict[str, Any]:
    ns: dict[str, Any] = {}
    exec(source, ns)
    return ns


def test_global_variable():
    ns = namespace(
        'import cachify\n'
        'calls = []\n'
        'threshold = 10\n'
        'def is_big(n):\n'
        '    return n > threshold\n'
        '@cachify.cache()\n'
        'def count_big(numbers):\n'
        '    calls.append(1)\n'
        '    return sum(1 for n in numbers if is_big(n))\n')
    count_big = ns['count_big']
    data = (5, 12, 30)
    assert count_big(data) == 2
    assert count_big(data) == 2
    ns['threshold'] = 20
    assert count_big(data) == 1
    assert count_big(data) == 1
    assert len(ns['calls']) == 2


def test_unrelated_global():
    ns = namespace(
        'import cachify\n'
        'calls = []\n'
        'a = 1\n'
        'b = 2\n'
        '@cachify.cache()\n'
        'def f():\n'
        '    calls.append(1)\n'
        '    return a\n')
    ns['f']()
    ns['b'] = 3
    ns['unused'] = 4
    assert ns['f']() == 1
    assert len(ns['calls']) == 1


def test_redefined_function():
    ns = namespace(
        'import cachify\n'
        'def helper():\n'
        '    return 1\n'
        '@cachify.cache()\n'
        'def f():\n'
        '    return helper()\n')
    assert ns['f']() == 1
    exec('def helper():\n    return 2\n', ns)
    assert ns['f']() == 2


def test_shadowed_builtin():
    ns = namespace(
        'import cachify\n'
        '@cachify.cache()\n'
        'def f():\n'
        '    return len("abc")\n')
    assert ns['f']() == 3
    ns['len'] = lambda _: 42
    assert ns['f']() == 42
    del ns['len']
    assert ns['f']() == 3


def test_globals_of_other_module():
    lib = namespace(
        'scale = 2\n'
        'def double(n):\n'
        '    return n * scale\n')
    app = namespace('import cachify\n')
    app['double'] = lib['double']
    exec('@cachify.cache()\n'
         'def f(n):\n'
         '    return double(n)\n', app)
    assert app['f'](5) == 10
    lib['scale'] = 3
    assert app['f'](5) == 15


def test_monitoring_off_outside_cached_calls():
    f = cachify.cache()(lambda: 1)
    f()
    tool = cachify._globals.tool
    assert tool is not None
    assert sys.monitoring.get_events(tool) == 0


def test_file_contents(tmp):
    path = os.path.join(tmp, 'data.txt')
    write(path, 'one')
    calls = []

    @cachify.cache()
    def read(path):
        calls.append(1)
        with open(path) as f:
            return f.read()

    def first_read():
        assert read(path) == 'one'

    assert_no_untracked(first_read)
    assert read(path) == 'one'
    write(path, 'three')
    assert read(path) == 'three'
    assert len(calls) == 2


def test_pathlib_read(tmp_path):
    path = tmp_path / 'data.txt'
    path.write_text('one')
    f = cachify.cache()(lambda: path.read_text())
    assert f() == 'one'
    path.write_text('three')
    assert f() == 'three'


def test_missing_file_created(tmp):
    @cachify.cache()
    def read(path):
        try:
            with open(path) as f:
                return f.read()
        except FileNotFoundError:
            return None

    path = os.path.join(tmp, 'later.txt')
    assert read(path) is None
    write(path, 'here')
    assert read(path) == 'here'


def test_directory_listing(tmp):
    write(os.path.join(tmp, 'a.txt'), '')

    @cachify.cache()
    def names(path):
        return sorted(os.listdir(path))

    assert_no_untracked(lambda: names(tmp))
    assert names(tmp) == ['a.txt']
    write(os.path.join(tmp, 'b.txt'), '')
    assert names(tmp) == ['a.txt', 'b.txt']


def test_scandir(tmp):
    @cachify.cache()
    def count(path):
        with os.scandir(path) as it:
            return len(list(it))

    assert count(tmp) == 0
    write(os.path.join(tmp, 'a.txt'), '')
    assert count(tmp) == 1


def test_environment_variable(unset_env):
    name = unset_env('CACHIFY_TEST_READ')
    calls = []

    @cachify.cache()
    def read():
        calls.append(1)
        return os.environ.get(name), os.getenv(name)

    assert read() == (None, None)
    os.environ[name] = 'a'
    assert read() == ('a', 'a')
    assert read() == ('a', 'a')
    os.environ[name] = 'b'
    assert read() == ('b', 'b')
    assert len(calls) == 3


def test_unrelated_environment_variable(unset_env):
    other = unset_env('CACHIFY_TEST_OTHER')
    calls = []

    @cachify.cache()
    def read():
        calls.append(1)
        return 'CACHIFY_TEST_READ' in os.environ

    read()
    os.environ[other] = '1'
    read()
    assert len(calls) == 1


def test_whole_environment(unset_env):
    name = unset_env('CACHIFY_TEST_ALL')
    f = cachify.cache()(lambda: dict(os.environ))
    assert name not in f()
    os.environ[name] = '1'
    assert name in f()


def test_environ_identity_preserved():
    from os import environ
    assert environ is os.environ
    assert isinstance(os.environ, os._Environ)


def test_imported_through_unnormalized_path(tmp_path):
    # Cachify must skip its own code even when the path it was imported from
    # contains '..', or its internal state would look like changed globals.
    root = os.path.dirname(os.path.dirname(os.path.abspath(cachify.__file__)))
    script = tmp_path / 'script.py'
    script.write_text(
        'import sys\n'
        f'sys.path.insert(0, {os.path.join(root, "tests", "..")!r})\n'
        'import cachify\n'
        'calls = []\n'
        '@cachify.cache()\n'
        'def f():\n'
        '    calls.append(1)\n'
        '    return 1\n'
        'f()\n'
        'f()\n'
        'print(len(calls))\n')
    result = subprocess.run([sys.executable, '-I', str(script)],
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == '1'
