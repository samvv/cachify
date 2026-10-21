from decimal import Decimal

import pytest

import cachify


def test_with_parentheses():
    calls = []

    @cachify.cache()
    def square(x):
        calls.append(x)
        return x * x

    assert square(3) == 9
    assert square(3) == 9
    assert calls == [3]
    assert square.cache_info() == cachify.CacheInfo(1, 1, 128, 1)


def test_without_parentheses():
    @cachify.cache
    def square(x):
        return x * x

    assert square(4) == 16
    assert square(4) == 16
    assert square.cache_info() == (1, 1, 128, 1)


def test_wraps_function():
    def f(x):
        """docstring"""
        return x

    g = cachify.cache()(f)
    assert g.__wrapped__ is f
    assert g.__name__ == 'f'
    assert g.__doc__ == 'docstring'


def test_lru_eviction():
    calls = []

    @cachify.cache(maxsize=2)
    def f(x):
        calls.append(x)
        return x

    f(1); f(2)
    f(1)        # 1 becomes most recently used
    f(3)        # evicts 2
    f(1)
    f(2)        # miss
    assert calls == [1, 2, 3, 2]
    assert f.cache_info().currsize == 2


def test_maxsize_zero():
    calls = []

    @cachify.cache(maxsize=0)
    def f(x):
        calls.append(x)
        return x

    f(1); f(1)
    assert calls == [1, 1]
    assert f.cache_info() == (0, 2, 0, 0)


def test_negative_maxsize():
    f = cachify.cache(maxsize=-5)(lambda x: x)
    assert f.cache_parameters()['maxsize'] == 0


def test_unbounded():
    f = cachify.cache(maxsize=None)(lambda x: x)
    for i in range(1000):
        f(i)
    assert f.cache_info().currsize == 1000


def test_typed():
    f = cachify.cache(typed=True)(lambda x: x)
    f(3); f(3.0); f(Decimal(3))
    assert f.cache_info().misses == 3

    g = cachify.cache(typed=False)(lambda x: x)
    g(3.0); g(Decimal(3))
    assert g.cache_info().hits == 1


def test_kwargs():
    f = cachify.cache()(lambda a, b=0: a + b)
    assert f(1, b=2) == 3
    assert f(1, b=2) == 3
    assert f.cache_info().hits == 1


def test_cache_clear():
    f = cachify.cache()(lambda x: x)
    f(1); f(1)
    f.cache_clear()
    assert f.cache_info() == (0, 0, 128, 0)


def test_cache_parameters():
    f = cachify.cache(maxsize=10, typed=True)(lambda x: x)
    assert f.cache_parameters() == {
        'maxsize': 10, 'typed': True, 'cycle_initial': None}


def test_unhashable_argument():
    f = cachify.cache()(lambda x: x)
    with pytest.raises(TypeError):
        f([])


def test_exception_not_cached():
    calls = []

    @cachify.cache()
    def f(x):
        calls.append(x)
        raise ValueError

    for _ in range(2):
        with pytest.raises(ValueError):
            f(1)
    assert calls == [1, 1]
    assert f.cache_info().currsize == 0


def test_recursion():
    @cachify.cache(maxsize=None)
    def fib(n):
        return n if n < 2 else fib(n - 1) + fib(n - 2)

    assert fib(100) == 354224848179261915075
    assert fib.cache_info().misses == 101


def test_invalid_maxsize():
    with pytest.raises(TypeError):
        cachify.cache('x')
    with pytest.raises(TypeError):
        cachify.cache()(42)


def test_rejects_generators_and_coroutines():
    def gen():
        yield 1

    async def coro():
        pass

    async def agen():
        yield 1

    for f in gen, coro, agen:
        with pytest.raises(TypeError):
            cachify.cache()(f)


def test_method():
    class A:
        def __init__(self, n):
            self.n = n

        @cachify.cache()
        def double(self):
            return self.n * 2

    a = A(3)
    assert a.double() == 6
    assert a.double() == 6
    assert A.double.cache_info().hits == 1
