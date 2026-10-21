import warnings

import cachify


def _untracked(func):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        func()
    return caught, [w for w in caught
                    if issubclass(w.category, cachify.UntrackedEffectWarning)]


def assert_warns_untracked(func, *fragments):
    """Run *func* and check that it issues one UntrackedEffectWarning."""
    caught, untracked = _untracked(func)
    assert len(untracked) == 1, [str(w.message) for w in caught]
    for fragment in fragments:
        assert fragment in str(untracked[0].message)
    return untracked[0]


def assert_no_untracked(func):
    """Run *func* and check that it issues no UntrackedEffectWarning."""
    _, untracked = _untracked(func)
    assert [str(w.message) for w in untracked] == []
