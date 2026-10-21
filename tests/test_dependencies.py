
import pytest

import cachify


def test_input_get_and_set():
    x = cachify.Input(1)
    assert x.get() == 1
    x.set(2)
    assert x.get() == 2
    assert repr(x) == 'Input(2)'


def test_rerun_when_input_changes():
    x = cachify.Input(1)
    calls = []

    @cachify.cache()
    def f():
        calls.append(1)
        return x.get() * 2

    assert f() == 2
    assert f() == 2
    x.set(5)
    assert f() == 10
    assert f() == 10
    assert len(calls) == 2


def test_unrelated_input_change():
    a = cachify.Input(1)
    b = cachify.Input(2)
    calls = []

    @cachify.cache()
    def f():
        calls.append(1)
        return a.get()

    f()
    b.set(3)
    assert f() == 1
    assert len(calls) == 1
    assert f.cache_info().hits == 1


def test_transitive_dependency():
    x = cachify.Input(1)
    calls = []

    @cachify.cache()
    def f():
        calls.append('f')
        return x.get() + 1

    @cachify.cache()
    def g():
        calls.append('g')
        return f() * 10

    assert g() == 20
    x.set(2)
    assert g() == 30
    assert calls == ['g', 'f', 'f', 'g']


def test_early_cutoff():
    x = cachify.Input(1)
    calls = []

    @cachify.cache()
    def parity():
        calls.append('parity')
        return x.get() % 2

    @cachify.cache()
    def describe():
        calls.append('describe')
        return 'odd' if parity() else 'even'

    assert describe() == 'odd'
    x.set(3)
    assert describe() == 'odd'
    assert calls == ['describe', 'parity', 'parity']
    x.set(4)
    assert describe() == 'even'
    assert calls[-2:] == ['parity', 'describe']


def test_uncached_helper_reads_are_tracked():
    x = cachify.Input(1)
    calls = []

    def helper():
        return x.get()

    @cachify.cache()
    def f():
        calls.append(1)
        return helper()

    assert f() == 1
    x.set(2)
    assert f() == 2
    assert len(calls) == 2


def test_dependencies_with_arguments():
    inputs = {name: cachify.Input(i) for i, name in enumerate('abc')}
    calls = []

    @cachify.cache()
    def read(name):
        calls.append(name)
        return inputs[name].get()

    @cachify.cache()
    def total():
        return read('a') + read('b') + read('c')

    assert total() == 3
    inputs['b'].set(10)
    assert total() == 12
    assert calls == ['a', 'b', 'c', 'b']


def test_dependencies_can_change():
    flag = cachify.Input(True)
    a = cachify.Input('a')
    b = cachify.Input('b')
    calls = []

    @cachify.cache()
    def pick():
        calls.append(1)
        return a.get() if flag.get() else b.get()

    assert pick() == 'a'
    b.set('B')                      # not read yet
    assert pick() == 'a'
    flag.set(False)
    assert pick() == 'B'
    a.set('A')                      # no longer read
    assert pick() == 'B'
    assert len(calls) == 2


def test_evicted_value_keeps_dependencies():
    x = cachify.Input(1)
    y = cachify.Input(1)
    calls = []

    @cachify.cache(maxsize=1)
    def f(n):
        calls.append(n)
        return x.get() + n

    @cachify.cache()
    def g():
        return f(1)

    assert g() == 2
    f(2)                # evicts the value of f(1)
    y.set(2)            # new revision, unrelated to f
    assert g() == 2
    assert calls == [1, 2]
    assert f(1) == 2
    assert calls == [1, 2, 1]


def test_cycle():
    @cachify.cache()
    def f(n):
        return f(n)

    with pytest.raises(cachify.CycleError):
        f(1)
    # The failed call is not cached
    assert f.cache_info().currsize == 0


def test_cycle_after_input_change():
    x = cachify.Input(False)

    @cachify.cache()
    def f():
        return g()

    @cachify.cache()
    def g():
        return f() if x.get() else 1

    assert f() == 1
    x.set(True)
    with pytest.raises(cachify.CycleError):
        f()


def test_recover_after_exception():
    x = cachify.Input(1)
    calls = []

    @cachify.cache()
    def f():
        if x.get() < 0:
            raise ValueError
        return 'ok'

    @cachify.cache()
    def g():
        calls.append(1)
        return f()

    assert g() == 'ok'
    x.set(-1)
    with pytest.raises(ValueError):
        g()
    x.set(2)
    # f returns the same value as before, so g doesn't need to rerun
    assert g() == 'ok'
    assert len(calls) == 1


def test_set_input_while_running():
    x = cachify.Input(1)

    @cachify.cache()
    def f():
        x.set(2)

    with pytest.raises(RuntimeError):
        f()
    assert x.get() == 1


def test_cache_clear_reruns_dependents():
    x = cachify.Input(1)
    calls = []

    @cachify.cache()
    def f():
        return 1

    @cachify.cache()
    def g():
        calls.append(1)
        return f()

    g()
    f.cache_clear()
    x.set(2)
    g()
    assert len(calls) == 2


def test_maxsize_zero_tracks_dependencies():
    x = cachify.Input(1)
    f = cachify.cache(maxsize=0)(lambda: x.get())
    assert f() == 1
    x.set(2)
    assert f() == 2
