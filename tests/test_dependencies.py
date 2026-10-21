import unittest

import cachify


class TestDependencies(unittest.TestCase):

    def test_input_get_and_set(self):
        x = cachify.Input(1)
        self.assertEqual(x.get(), 1)
        x.set(2)
        self.assertEqual(x.get(), 2)
        self.assertEqual(repr(x), 'Input(2)')

    def test_rerun_when_input_changes(self):
        x = cachify.Input(1)
        calls = []

        @cachify.cache()
        def f():
            calls.append(1)
            return x.get() * 2

        self.assertEqual(f(), 2)
        self.assertEqual(f(), 2)
        x.set(5)
        self.assertEqual(f(), 10)
        self.assertEqual(f(), 10)
        self.assertEqual(len(calls), 2)

    def test_unrelated_input_change(self):
        a = cachify.Input(1)
        b = cachify.Input(2)
        calls = []

        @cachify.cache()
        def f():
            calls.append(1)
            return a.get()

        f()
        b.set(3)
        self.assertEqual(f(), 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(f.cache_info().hits, 1)

    def test_transitive_dependency(self):
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

        self.assertEqual(g(), 20)
        x.set(2)
        self.assertEqual(g(), 30)
        self.assertEqual(calls, ['g', 'f', 'f', 'g'])

    def test_early_cutoff(self):
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

        self.assertEqual(describe(), 'odd')
        x.set(3)
        self.assertEqual(describe(), 'odd')
        self.assertEqual(calls, ['describe', 'parity', 'parity'])
        x.set(4)
        self.assertEqual(describe(), 'even')
        self.assertEqual(calls[-2:], ['parity', 'describe'])

    def test_uncached_helper_reads_are_tracked(self):
        x = cachify.Input(1)
        calls = []

        def helper():
            return x.get()

        @cachify.cache()
        def f():
            calls.append(1)
            return helper()

        self.assertEqual(f(), 1)
        x.set(2)
        self.assertEqual(f(), 2)
        self.assertEqual(len(calls), 2)

    def test_dependencies_with_arguments(self):
        inputs = {name: cachify.Input(i) for i, name in enumerate('abc')}
        calls = []

        @cachify.cache()
        def read(name):
            calls.append(name)
            return inputs[name].get()

        @cachify.cache()
        def total():
            return read('a') + read('b') + read('c')

        self.assertEqual(total(), 3)
        inputs['b'].set(10)
        self.assertEqual(total(), 12)
        self.assertEqual(calls, ['a', 'b', 'c', 'b'])

    def test_dependencies_can_change(self):
        flag = cachify.Input(True)
        a = cachify.Input('a')
        b = cachify.Input('b')
        calls = []

        @cachify.cache()
        def pick():
            calls.append(1)
            return a.get() if flag.get() else b.get()

        self.assertEqual(pick(), 'a')
        b.set('B')                      # not read yet
        self.assertEqual(pick(), 'a')
        flag.set(False)
        self.assertEqual(pick(), 'B')
        a.set('A')                      # no longer read
        self.assertEqual(pick(), 'B')
        self.assertEqual(len(calls), 2)

    def test_evicted_value_keeps_dependencies(self):
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

        self.assertEqual(g(), 2)
        f(2)                # evicts the value of f(1)
        y.set(2)            # new revision, unrelated to f
        self.assertEqual(g(), 2)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(f(1), 2)
        self.assertEqual(calls, [1, 2, 1])

    def test_cycle(self):
        @cachify.cache()
        def f(n):
            return f(n)

        with self.assertRaises(cachify.CycleError):
            f(1)
        # The failed call is not cached
        self.assertEqual(f.cache_info().currsize, 0)

    def test_cycle_after_input_change(self):
        x = cachify.Input(False)

        @cachify.cache()
        def f():
            return g()

        @cachify.cache()
        def g():
            return f() if x.get() else 1

        self.assertEqual(f(), 1)
        x.set(True)
        with self.assertRaises(cachify.CycleError):
            f()

    def test_recover_after_exception(self):
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

        self.assertEqual(g(), 'ok')
        x.set(-1)
        with self.assertRaises(ValueError):
            g()
        x.set(2)
        # f returns the same value as before, so g doesn't need to rerun
        self.assertEqual(g(), 'ok')
        self.assertEqual(len(calls), 1)

    def test_set_input_while_running(self):
        x = cachify.Input(1)

        @cachify.cache()
        def f():
            x.set(2)

        with self.assertRaises(RuntimeError):
            f()
        self.assertEqual(x.get(), 1)

    def test_cache_clear_reruns_dependents(self):
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
        self.assertEqual(len(calls), 2)

    def test_maxsize_zero_tracks_dependencies(self):
        x = cachify.Input(1)
        f = cachify.cache(maxsize=0)(lambda: x.get())
        self.assertEqual(f(), 1)
        x.set(2)
        self.assertEqual(f(), 2)


if __name__ == '__main__':
    unittest.main()
