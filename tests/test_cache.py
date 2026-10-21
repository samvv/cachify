import unittest
from decimal import Decimal

import cachify


class TestCache(unittest.TestCase):

    def test_with_parentheses(self):
        calls = []

        @cachify.cache()
        def square(x):
            calls.append(x)
            return x * x

        self.assertEqual(square(3), 9)
        self.assertEqual(square(3), 9)
        self.assertEqual(calls, [3])
        self.assertEqual(square.cache_info(), cachify.CacheInfo(1, 1, 128, 1))

    def test_without_parentheses(self):
        @cachify.cache
        def square(x):
            return x * x

        self.assertEqual(square(4), 16)
        self.assertEqual(square(4), 16)
        self.assertEqual(square.cache_info(), (1, 1, 128, 1))

    def test_wraps_function(self):
        def f(x):
            """docstring"""
            return x

        g = cachify.cache()(f)
        self.assertIs(g.__wrapped__, f)
        self.assertEqual(g.__name__, 'f')
        self.assertEqual(g.__doc__, 'docstring')

    def test_lru_eviction(self):
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
        self.assertEqual(calls, [1, 2, 3, 2])
        self.assertEqual(f.cache_info().currsize, 2)

    def test_maxsize_zero(self):
        calls = []

        @cachify.cache(maxsize=0)
        def f(x):
            calls.append(x)
            return x

        f(1); f(1)
        self.assertEqual(calls, [1, 1])
        self.assertEqual(f.cache_info(), (0, 2, 0, 0))

    def test_negative_maxsize(self):
        f = cachify.cache(maxsize=-5)(lambda x: x)
        self.assertEqual(f.cache_parameters()['maxsize'], 0)

    def test_unbounded(self):
        f = cachify.cache(maxsize=None)(lambda x: x)
        for i in range(1000):
            f(i)
        self.assertEqual(f.cache_info().currsize, 1000)

    def test_typed(self):
        f = cachify.cache(typed=True)(lambda x: x)
        f(3); f(3.0); f(Decimal(3))
        self.assertEqual(f.cache_info().misses, 3)

        g = cachify.cache(typed=False)(lambda x: x)
        g(3.0); g(Decimal(3))
        self.assertEqual(g.cache_info().hits, 1)

    def test_kwargs(self):
        f = cachify.cache()(lambda a, b=0: a + b)
        self.assertEqual(f(1, b=2), 3)
        self.assertEqual(f(1, b=2), 3)
        self.assertEqual(f.cache_info().hits, 1)

    def test_cache_clear(self):
        f = cachify.cache()(lambda x: x)
        f(1); f(1)
        f.cache_clear()
        self.assertEqual(f.cache_info(), (0, 0, 128, 0))

    def test_cache_parameters(self):
        f = cachify.cache(maxsize=10, typed=True)(lambda x: x)
        self.assertEqual(f.cache_parameters(), {'maxsize': 10, 'typed': True,
                                                'cycle_initial': None})

    def test_unhashable_argument(self):
        f = cachify.cache()(lambda x: x)
        with self.assertRaises(TypeError):
            f([])

    def test_exception_not_cached(self):
        calls = []

        @cachify.cache()
        def f(x):
            calls.append(x)
            raise ValueError

        for _ in range(2):
            with self.assertRaises(ValueError):
                f(1)
        self.assertEqual(calls, [1, 1])
        self.assertEqual(f.cache_info().currsize, 0)

    def test_recursion(self):
        @cachify.cache(maxsize=None)
        def fib(n):
            return n if n < 2 else fib(n - 1) + fib(n - 2)

        self.assertEqual(fib(100), 354224848179261915075)
        self.assertEqual(fib.cache_info().misses, 101)

    def test_invalid_maxsize(self):
        with self.assertRaises(TypeError):
            cachify.cache('x')
        with self.assertRaises(TypeError):
            cachify.cache()(42)

    def test_rejects_generators_and_coroutines(self):
        def gen():
            yield 1

        async def coro():
            pass

        async def agen():
            yield 1

        for f in gen, coro, agen:
            with self.assertRaises(TypeError):
                cachify.cache()(f)

    def test_method(self):
        class A:
            def __init__(self, n):
                self.n = n

            @cachify.cache()
            def double(self):
                return self.n * 2

        a = A(3)
        self.assertEqual(a.double(), 6)
        self.assertEqual(a.double(), 6)
        self.assertEqual(A.double.cache_info().hits, 1)


if __name__ == '__main__':
    unittest.main()
