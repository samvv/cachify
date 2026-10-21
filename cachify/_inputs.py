"""Inputs: the state cached results depend on."""

import os

from . import _state


class BaseInput:
    __slots__ = ('_changed_at', '__weakref__')


class Input(BaseInput):
    """A value that cached functions can depend on.

    Most state is tracked automatically.  Use Input for state that cachify
    cannot see, such as the contents of a mutable object.

    Read it with get().  Changing it with set() marks every cached result
    that read it as possibly out of date.
    """

    __slots__ = ('_value',)

    def __init__(self, value):
        with _state.lock:
            self._value = value
            self._changed_at = _state.revision

    def get(self):
        """Return the value and record it as a dependency."""
        with _state.lock:
            _state.record(self)
            return self._value

    def set(self, value):
        """Change the value."""
        with _state.lock:
            if _state.active.get() is not None:
                raise RuntimeError(
                    'cannot change an Input while a cached function is running')
            _state.revision += 1
            self._value = value
            self._changed_at = _state.revision

    def __repr__(self):
        return f'{type(self).__name__}({self._value!r})'


class PolledInput(BaseInput):
    """An input that cachify creates and checks for changes by itself."""

    __slots__ = ('_state',)

    def __init__(self):
        self._state = self._read()
        self._changed_at = _state.revision

    def _read(self):
        raise NotImplementedError

    def _same(self, a, b):
        return a == b

    def _poll(self, revision):
        """Return True and stamp *revision* if the state changed."""
        state = self._read()
        if self._same(state, self._state):
            return False
        self._state = state
        self._changed_at = revision
        return True


class GlobalInput(PolledInput):
    """The value of a global variable, or of a builtin if it has none."""

    __slots__ = ('_globals', '_builtins', '_name')

    def __init__(self, globals, builtins, name):
        self._globals = globals
        self._builtins = builtins
        self._name = name
        super().__init__()

    def _read(self):
        value = self._globals.get(self._name, _state.MISSING)
        if value is _state.MISSING:
            value = self._builtins.get(self._name, _state.MISSING)
        return value

    def _same(self, a, b):
        return a is b


class PathInput(PolledInput):
    """The metadata of a file or directory."""

    __slots__ = ('_path',)

    def __init__(self, path):
        self._path = path
        super().__init__()

    def _read(self):
        try:
            st = os.stat(self._path)
        except (OSError, ValueError):
            return None
        return (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


class EnvInput(PolledInput):
    """One environment variable, or the whole environment if key is None."""

    __slots__ = ('_environ', '_key')

    def __init__(self, environ, key):
        self._environ = environ
        self._key = key
        super().__init__()

    def _read(self):
        data = self._environ._data
        if self._key is None:
            return dict(data)
        return data.get(self._key, _state.MISSING)


# Every input cachify created, keyed by what it watches.
_polled_inputs = {}


def track(key, make_input, *args):
    """Record the polled input for *key*, creating it if needed."""
    inp = _polled_inputs.get(key)
    if inp is None:
        inp = _polled_inputs[key] = make_input(*args)
    _state.record(inp)


def track_path(path):
    """Record the file or directory at *path*.

    Return False if *path* is not a path, such as a file descriptor.
    """
    try:
        path = os.path.abspath(os.fspath(path))
    except (TypeError, ValueError):
        return False
    track(('path', path), PathInput, path)
    return True


def poll_inputs():
    """Start a new revision if any polled input changed."""
    revision = _state.revision + 1
    changed = False
    for inp in _polled_inputs.values():
        if inp._poll(revision):
            changed = True
    if changed:
        _state.revision = revision
