import unittest

import cachify


class TestCycleInitial(unittest.TestCase):

    def test_without_cycle_initial(self):
        @cachify.cache()
        def f():
            return f()

        with self.assertRaises(cachify.CycleError):
            f()

    def test_fixpoint(self):
        edges = {1: [2], 2: [3, 1], 3: []}
        calls = []

        @cachify.cache(cycle_initial=lambda n: frozenset())
        def reach(n):
            calls.append(n)
            return frozenset({n}).union(*(reach(m) for m in edges[n]))

        self.assertEqual(reach(1), {1, 2, 3})
        # reach(1) and reach(2) ran twice, reach(3) once
        self.assertEqual(sorted(calls), [1, 1, 2, 2, 3])
        # The result computed inside the cycle is final once it converges
        self.assertEqual(reach(2), {1, 2, 3})
        self.assertEqual(reach(1), {1, 2, 3})
        self.assertEqual(len(calls), 5)

    def test_initial_gets_arguments(self):
        seen = []

        def initial(n, *, scale):
            seen.append((n, scale))
            return 0

        @cachify.cache(cycle_initial=initial)
        def f(n, *, scale):
            return min(f(n, scale=scale) + 1, n * scale)

        self.assertEqual(f(2, scale=3), 6)
        self.assertEqual(seen, [(2, 3)])

    def test_initial_called_once_per_run(self):
        seen = []

        def initial():
            seen.append(1)
            return 0

        @cachify.cache(cycle_initial=initial)
        def f():
            return min(f() + 1, 5)

        self.assertEqual(f(), 5)
        self.assertEqual(seen, [1])

    def test_no_convergence(self):
        @cachify.cache(cycle_initial=lambda: 0)
        def f():
            return f() + 1

        with self.assertRaises(cachify.CycleError):
            f()
        self.assertEqual(f.cache_info().currsize, 0)

    def test_head_without_initial(self):
        # The call that is re-entered needs cycle_initial
        @cachify.cache(cycle_initial=lambda: 0)
        def f():
            return g()

        @cachify.cache()
        def g():
            return f()

        with self.assertRaises(cachify.CycleError):
            g()

    def test_through_other_function(self):
        @cachify.cache(cycle_initial=lambda: 0)
        def f():
            return min(g() + 1, 3)

        @cachify.cache()
        def g():
            return f()

        self.assertEqual(f(), 3)
        self.assertEqual(g(), 3)

    def test_input_change(self):
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

        self.assertEqual(h(), 33)
        limit.set(7)
        self.assertEqual(h(), 77)
        self.assertEqual(g(), 7)
        limit.set(2)
        self.assertEqual(g(), 2)
        self.assertEqual(h(), 22)

    def test_unchanged_after_input_change(self):
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

        self.assertEqual(h(), 3)
        calls.clear()
        other.set(1)
        # Only h read the input, so the cycle is reused
        self.assertEqual(h(), 3)
        self.assertEqual(calls, [])

    def test_head_entered_from_other_side(self):
        # The first call into the cycle becomes its head, so it needs
        # cycle_initial.
        @cachify.cache(cycle_initial=lambda: 0)
        def f():
            return min(g() + 1, 3)

        @cachify.cache()
        def g():
            return f()

        with self.assertRaises(cachify.CycleError):
            g()
        self.assertEqual(f(), 3)
        self.assertEqual(g(), 3)

    def test_initial_reads_are_tracked(self):
        start = cachify.Input(0)

        @cachify.cache(cycle_initial=lambda: start.get())
        def f():
            return max(f(), 2)

        self.assertEqual(f(), 2)
        start.set(5)
        self.assertEqual(f(), 5)

    def test_initial_depends_on_head(self):
        @cachify.cache(cycle_initial=lambda: f())
        def f():
            return f()

        with self.assertRaises(cachify.CycleError):
            f()

    def test_nested_cycles(self):
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

        self.assertEqual(a(), 10)
        self.assertEqual(b(), 10)
        self.assertEqual(c(), 4)

    def test_exception_in_cycle(self):
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

        with self.assertRaises(ValueError):
            f()
        fail.set(False)
        self.assertEqual(f(), 2)
        self.assertEqual(g(), 2)

    def test_not_callable(self):
        with self.assertRaises(TypeError):
            cachify.cache(cycle_initial=1)


if __name__ == '__main__':
    unittest.main()
