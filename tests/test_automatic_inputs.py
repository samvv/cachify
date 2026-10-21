import os
import pathlib
import sys
import tempfile
import unittest

import cachify
import cachify._globals
from helpers import UntrackedAssertions


class TestAutomaticInputs(UntrackedAssertions, unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def write(self, name, text):
        with open(self.path(name), 'w') as f:
            f.write(text)

    def namespace(self, source):
        ns = {}
        exec(source, ns)
        return ns

    def test_global_variable(self):
        ns = self.namespace(
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
        self.assertEqual(count_big(data), 2)
        self.assertEqual(count_big(data), 2)
        ns['threshold'] = 20
        self.assertEqual(count_big(data), 1)
        self.assertEqual(count_big(data), 1)
        self.assertEqual(len(ns['calls']), 2)

    def test_unrelated_global(self):
        ns = self.namespace(
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
        self.assertEqual(ns['f'](), 1)
        self.assertEqual(len(ns['calls']), 1)

    def test_redefined_function(self):
        ns = self.namespace(
            'import cachify\n'
            'def helper():\n'
            '    return 1\n'
            '@cachify.cache()\n'
            'def f():\n'
            '    return helper()\n')
        self.assertEqual(ns['f'](), 1)
        exec('def helper():\n    return 2\n', ns)
        self.assertEqual(ns['f'](), 2)

    def test_shadowed_builtin(self):
        ns = self.namespace(
            'import cachify\n'
            '@cachify.cache()\n'
            'def f():\n'
            '    return len("abc")\n')
        self.assertEqual(ns['f'](), 3)
        ns['len'] = lambda s: 42
        self.assertEqual(ns['f'](), 42)
        del ns['len']
        self.assertEqual(ns['f'](), 3)

    def test_globals_of_other_module(self):
        lib = self.namespace(
            'scale = 2\n'
            'def double(n):\n'
            '    return n * scale\n')
        app = self.namespace('import cachify\n')
        app['double'] = lib['double']
        exec('@cachify.cache()\n'
             'def f(n):\n'
             '    return double(n)\n', app)
        self.assertEqual(app['f'](5), 10)
        lib['scale'] = 3
        self.assertEqual(app['f'](5), 15)

    def test_monitoring_off_outside_cached_calls(self):
        f = cachify.cache()(lambda: 1)
        f()
        tool = cachify._globals.tool
        self.assertIsNotNone(tool)
        self.assertEqual(sys.monitoring.get_events(tool), 0)

    def test_file_contents(self):
        self.write('data.txt', 'one')
        calls = []

        @cachify.cache()
        def read(path):
            calls.append(1)
            with open(path) as f:
                return f.read()

        path = self.path('data.txt')
        self.assertNoUntracked(lambda: self.assertEqual(read(path), 'one'))
        self.assertEqual(read(path), 'one')
        self.write('data.txt', 'three')
        self.assertEqual(read(path), 'three')
        self.assertEqual(len(calls), 2)

    def test_pathlib_read(self):
        self.write('data.txt', 'one')
        path = pathlib.Path(self.path('data.txt'))
        f = cachify.cache()(lambda: path.read_text())
        self.assertEqual(f(), 'one')
        self.write('data.txt', 'three')
        self.assertEqual(f(), 'three')

    def test_missing_file_created(self):
        @cachify.cache()
        def read(path):
            try:
                with open(path) as f:
                    return f.read()
            except FileNotFoundError:
                return None

        path = self.path('later.txt')
        self.assertIsNone(read(path))
        self.write('later.txt', 'here')
        self.assertEqual(read(path), 'here')

    def test_directory_listing(self):
        self.write('a.txt', '')

        @cachify.cache()
        def names(path):
            return sorted(os.listdir(path))

        self.assertNoUntracked(lambda: names(self.tmp.name))
        self.assertEqual(names(self.tmp.name), ['a.txt'])
        self.write('b.txt', '')
        self.assertEqual(names(self.tmp.name), ['a.txt', 'b.txt'])

    def test_scandir(self):
        @cachify.cache()
        def count(path):
            with os.scandir(path) as it:
                return len(list(it))

        self.assertEqual(count(self.tmp.name), 0)
        self.write('a.txt', '')
        self.assertEqual(count(self.tmp.name), 1)

    def test_environment_variable(self):
        name = 'CACHIFY_TEST_READ'
        self.addCleanup(os.environ.pop, name, None)
        calls = []

        @cachify.cache()
        def read():
            calls.append(1)
            return os.environ.get(name), os.getenv(name)

        self.assertEqual(read(), (None, None))
        os.environ[name] = 'a'
        self.assertEqual(read(), ('a', 'a'))
        self.assertEqual(read(), ('a', 'a'))
        os.environ[name] = 'b'
        self.assertEqual(read(), ('b', 'b'))
        self.assertEqual(len(calls), 3)

    def test_unrelated_environment_variable(self):
        self.addCleanup(os.environ.pop, 'CACHIFY_TEST_OTHER', None)
        calls = []

        @cachify.cache()
        def read():
            calls.append(1)
            return 'CACHIFY_TEST_READ' in os.environ

        read()
        os.environ['CACHIFY_TEST_OTHER'] = '1'
        read()
        self.assertEqual(len(calls), 1)

    def test_whole_environment(self):
        name = 'CACHIFY_TEST_ALL'
        self.addCleanup(os.environ.pop, name, None)
        f = cachify.cache()(lambda: dict(os.environ))
        self.assertNotIn(name, f())
        os.environ[name] = '1'
        self.assertIn(name, f())

    def test_environ_identity_preserved(self):
        from os import environ
        self.assertIs(environ, os.environ)
        self.assertIsInstance(os.environ, os._Environ)


if __name__ == '__main__':
    unittest.main()
