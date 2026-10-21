"""Tracking of environment variables.

Reading os.environ raises no audit event, so os.environ is given a class that
records reads.  The object itself is kept, so references to it stay valid.
"""

import os

from . import _state
from ._inputs import EnvInput, track


class _TrackedEnviron(os._Environ):

    def __getitem__(self, key):
        if _state.active.get() is not None:
            try:
                encoded = self.encodekey(key)
            except Exception:
                pass    # the lookup below raises the error
            else:
                track(('env', id(self), encoded), EnvInput, self, encoded)
        return super().__getitem__(key)

    def __iter__(self):
        if _state.active.get() is not None:
            track(('env', id(self), None), EnvInput, self, None)
        return super().__iter__()

    def __len__(self):
        if _state.active.get() is not None:
            track(('env', id(self), None), EnvInput, self, None)
        return super().__len__()


for _environ in (os.environ, getattr(os, 'environb', None)):
    if type(_environ) is os._Environ:
        _environ.__class__ = _TrackedEnviron
del _environ
