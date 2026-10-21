import math

import pytest

import cachify


def test_without_cycle_initial():
    @cachify.cache()
    def f():
        return f()

    with pytest.raises(cachify.CycleError):
        f()


def test_fixpoint():
    edges = {1: [2], 2: [3, 1], 3: []}
    calls = []

    @cachify.cache(cycle_initial=lambda n: frozenset())
    def reach(n):
        calls.append(n)
        return frozenset({n}).union(*(reach(m) for m in edges[n]))

    assert reach(1) == {1, 2, 3}
    # reach(1) and reach(2) ran twice, reach(3) once
    assert sorted(calls) == [1, 1, 2, 2, 3]
    # The result computed inside the cycle is final once it converges
    assert reach(2) == {1, 2, 3}
    assert reach(1) == {1, 2, 3}
    assert len(calls) == 5


def test_initial_gets_arguments():
    seen = []

    def initial(n, *, scale):
        seen.append((n, scale))
        return 0

    @cachify.cache(cycle_initial=initial)
    def f(n, *, scale):
        return min(f(n, scale=scale) + 1, n * scale)

    assert f(2, scale=3) == 6
    assert seen == [(2, 3)]


def test_initial_called_once_per_run():
    seen = []

    def initial():
        seen.append(1)
        return 0

    @cachify.cache(cycle_initial=initial)
    def f():
        return min(f() + 1, 5)

    assert f() == 5
    assert seen == [1]


def test_no_convergence():
    @cachify.cache(cycle_initial=lambda: 0)
    def f():
        return f() + 1

    with pytest.raises(cachify.CycleError):
        f()
    assert f.cache_info().currsize == 0


def test_head_without_initial():
    # The call that is re-entered needs cycle_initial
    @cachify.cache(cycle_initial=lambda: 0)
    def f():
        return g()

    @cachify.cache()
    def g():
        return f()

    with pytest.raises(cachify.CycleError):
        g()


def test_through_other_function():
    @cachify.cache(cycle_initial=lambda: 0)
    def f():
        return min(g() + 1, 3)

    @cachify.cache()
    def g():
        return f()

    assert f() == 3
    assert g() == 3


def test_input_change():
    limit = cachify.Input(3)

    @cachify.cache(cycle_initial=lambda: 0)
    def f():
        return min(g() + 1, limit.get())

    @cachify.cache()
    def g():
        return f()

    @cachify.cache()
    def h():
        return f() + g() * 10

    assert h() == 33
    limit.set(7)
    assert h() == 77
    assert g() == 7
    limit.set(2)
    assert g() == 2
    assert h() == 22


def test_unchanged_after_input_change():
    other = cachify.Input(0)
    calls = []

    @cachify.cache(cycle_initial=lambda: 0)
    def f():
        calls.append('f')
        return min(g() + 1, 3)

    @cachify.cache()
    def g():
        calls.append('g')
        return f()

    @cachify.cache()
    def h():
        other.get()
        return f()

    assert h() == 3
    calls.clear()
    other.set(1)
    # Only h read the input, so the cycle is reused
    assert h() == 3
    assert calls == []


def test_head_entered_from_other_side():
    # The first call into the cycle becomes its head, so it needs
    # cycle_initial.
    @cachify.cache(cycle_initial=lambda: 0)
    def f():
        return min(g() + 1, 3)

    @cachify.cache()
    def g():
        return f()

    with pytest.raises(cachify.CycleError):
        g()
    assert f() == 3
    assert g() == 3


def test_initial_reads_are_tracked():
    start = cachify.Input(0)

    @cachify.cache(cycle_initial=lambda: start.get())
    def f():
        return max(f(), 2)

    assert f() == 2
    start.set(5)
    assert f() == 5


def test_initial_depends_on_head():
    @cachify.cache(cycle_initial=lambda: f())
    def f():
        return f()

    with pytest.raises(cachify.CycleError):
        f()


def test_nested_cycles():
    # a -> b -> a and b -> c -> b
    @cachify.cache(cycle_initial=lambda: 0)
    def a():
        return min(b() + 1, 10)

    @cachify.cache(cycle_initial=lambda: 0)
    def b():
        return max(a(), c())

    @cachify.cache()
    def c():
        return min(b(), 4)

    assert a() == 10
    assert b() == 10
    assert c() == 4


def test_exception_in_cycle():
    fail = cachify.Input(True)

    @cachify.cache(cycle_initial=lambda: 0)
    def f():
        v = g()
        if v > 0 and fail.get():
            raise ValueError
        return min(v + 1, 2)

    @cachify.cache()
    def g():
        return f()

    with pytest.raises(ValueError):
        f()
    fail.set(False)
    assert f() == 2
    assert g() == 2


def test_not_callable():
    with pytest.raises(TypeError):
        cachify.cache(cycle_initial=1)


def test_cycle_fn_widens():
    # Without cycle_fn this never converges: every run adds one.
    def widen(previous, value, iteration):
        return value if value <= 5 else math.inf

    @cachify.cache(cycle_initial=lambda: 0, cycle_fn=widen)
    def f():
        return f() + 1

    assert f() == math.inf


def test_cycle_fn_arguments():
    calls = []

    def cycle_fn(previous, value, iteration, n, *, scale):
        calls.append((previous, value, iteration, n, scale))
        return value

    @cachify.cache(cycle_initial=lambda n, *, scale: 0, cycle_fn=cycle_fn)
    def f(n, *, scale):
        return min(f(n, scale=scale) + 1, n * scale)

    assert f(1, scale=2) == 2
    # Called after every run, including the one that converged
    assert calls == [(0, 1, 0, 1, 2), (1, 2, 1, 1, 2), (2, 2, 2, 1, 2)]


def test_cycle_fn_result_is_used():
    @cachify.cache(cycle_initial=lambda: 0,
                   cycle_fn=lambda previous, value, iteration: 'done')
    def f():
        f()
        return 'running'

    assert f() == 'done'


def test_cycle_fn_not_called_without_cycle():
    calls = []

    def cycle_fn(previous, value, iteration):
        calls.append(value)
        return value

    @cachify.cache(cycle_initial=lambda: 0, cycle_fn=cycle_fn)
    def f():
        return 1

    assert f() == 1
    assert calls == []


def test_cycle_fn_reads_are_tracked():
    limit = cachify.Input(3)

    @cachify.cache(cycle_initial=lambda: 0,
                   cycle_fn=lambda previous, value, iteration:
                       min(value, limit.get()))
    def f():
        return f() + 1

    assert f() == 3
    limit.set(5)
    assert f() == 5


def test_cycle_fn_requires_cycle_initial():
    with pytest.raises(TypeError):
        cachify.cache(cycle_fn=lambda previous, value, iteration: value)


def test_cycle_fn_not_callable():
    with pytest.raises(TypeError):
        cachify.cache(cycle_initial=lambda: 0, cycle_fn=1)
