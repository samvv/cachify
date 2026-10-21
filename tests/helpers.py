import warnings

import cachify


class UntrackedAssertions:
    """Assertions about UntrackedEffectWarning, mixed into test cases."""

    def assertWarnsUntracked(self, func, *fragments):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            func()
        untracked = [w for w in caught
                     if issubclass(w.category, cachify.UntrackedEffectWarning)]
        self.assertEqual(len(untracked), 1, [str(w.message) for w in caught])
        for fragment in fragments:
            self.assertIn(fragment, str(untracked[0].message))
        return untracked[0]

    def assertNoUntracked(self, func):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            func()
        self.assertEqual(
            [str(w.message) for w in caught
             if issubclass(w.category, cachify.UntrackedEffectWarning)], [])
