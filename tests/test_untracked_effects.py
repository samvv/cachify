import linecache
import os
import sys
import tempfile
import unittest
import warnings

import cachify
from helpers import UntrackedAssertions


class TestUntrackedEffects(UntrackedAssertions, unittest.TestCase):

    def test_open_for_writing(self):
        @cachify.cache()
        def read():
            with open(os.devnull, 'w') as f:
                return f.write('x')

        w = self.assertWarnsUntracked(read, 'read()', "'open'", repr(os.devnull))
        # The warning points at the code that opened the file
        self.assertEqual(w.filename, __file__)

    def test_not_cached_no_warning(self):
        def read():
            with open(os.devnull, 'w') as f:
                return f.write('x')

        self.assertNoUntracked(read)

    def test_cache_hit_no_warning(self):
        @cachify.cache()
        def read():
            with open(os.devnull, 'w') as f:
                return f.write('x')

        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            read()
        self.assertNoUntracked(read)

    def test_uncached_helper(self):
        def helper():
            return open(os.devnull, 'w').close()

        @cachify.cache()
        def outer():
            return helper()

        self.assertWarnsUntracked(outer, 'outer()', "'open'")

    def test_innermost_cached_function_is_named(self):
        @cachify.cache()
        def inner():
            return open(os.devnull, 'w').close()

        @cachify.cache()
        def outer():
            return inner()

        self.assertWarnsUntracked(outer, 'inner()')

    def test_environment_change(self):
        @cachify.cache()
        def set_env():
            os.environ['CACHIFY_TEST_VAR'] = '1'

        try:
            self.assertWarnsUntracked(set_env, "'os.putenv'")
        finally:
            os.environ.pop('CACHIFY_TEST_VAR', None)

    def test_pure_function_no_warning(self):
        @cachify.cache()
        def add(a, b):
            return a + b

        self.assertNoUntracked(lambda: add(1, 2))

    def test_import_no_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'cachify_test_mod.py'), 'w') as f:
                f.write('VALUE = 42\n')
            sys.path.insert(0, tmp)
            try:
                @cachify.cache()
                def load():
                    import cachify_test_mod
                    return cachify_test_mod.VALUE

                self.assertNoUntracked(load)
            finally:
                sys.path.remove(tmp)
                sys.modules.pop('cachify_test_mod', None)

    def test_warning_as_error(self):
        calls = []

        @cachify.cache()
        def read():
            calls.append(1)
            with open(os.devnull, 'w'):
                pass

        with warnings.catch_warnings():
            warnings.simplefilter('error', cachify.UntrackedEffectWarning)
            for _ in range(2):
                with self.assertRaises(cachify.UntrackedEffectWarning):
                    read()
        # The failed call is not cached
        self.assertEqual(len(calls), 2)

    def test_showing_warning_does_not_recurse(self):
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
        self.assertEqual(len(shown), 1)

    def test_showing_warning_adds_no_dependency(self):
        # Formatting a warning reads the source line from disk.  That file
        # must not become a dependency of the cached call.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'cachify_warn_mod.py')
            with open(path, 'w') as f:
                f.write('import os, cachify\n'
                        'calls = []\n'
                        '@cachify.cache()\n'
                        'def write():\n'
                        '    calls.append(1)\n'
                        '    open(os.devnull, "w").close()\n')
            sys.path.insert(0, tmp)
            self.addCleanup(sys.path.remove, tmp)
            self.addCleanup(sys.modules.pop, 'cachify_warn_mod', None)
            import cachify_warn_mod as mod

            def showwarning(message, category, filename, lineno, file=None,
                            line=None):
                warnings.formatwarning(message, category, filename, lineno,
                                       line)

            linecache.clearcache()
            with warnings.catch_warnings():
                warnings.simplefilter('always')
                warnings.showwarning = showwarning
                mod.write()
                with open(path, 'a') as f:
                    f.write('# changed\n')
                mod.write()
            self.assertEqual(len(mod.calls), 1)

if __name__ == '__main__':
    unittest.main()
