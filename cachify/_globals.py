"""Tracking of global variables.

While a cached function runs, sys.monitoring reports every Python function
that starts.  The global names that function's code can read are recorded as
dependencies.
"""

import dis
import sys
import warnings

from . import _state
from ._inputs import GlobalInput, track

_global_names_cache = {}


def _global_names(code):
    names = _global_names_cache.get(code)
    if names is None:
        names = tuple({instr.argval for instr in dis.get_instructions(code)
                       if instr.opname == 'LOAD_GLOBAL'})
        _global_names_cache[code] = names
    return names


def _on_py_start(code, offset, *, active=_state.active,
                 getframe=sys._getframe, skip=_state.PACKAGE_DIR):
    memo = active.get()
    if memo is None or code in memo.codes:
        return
    memo.codes.add(code)
    filename = code.co_filename
    if filename.startswith(skip) or filename.startswith('<frozen importlib.'):
        return
    names = _global_names(code)
    if not names:
        return
    frame = getframe(1)
    globals = frame.f_globals
    builtins = frame.f_builtins
    for name in names:
        track((id(globals), name), GlobalInput, globals, builtins, name)


def _use_tool():
    monitoring = sys.monitoring
    for tool in (4, 3):
        try:
            monitoring.use_tool_id(tool, 'cachify')
        except ValueError:
            continue
        monitoring.register_callback(tool, monitoring.events.PY_START,
                                     _on_py_start)
        return tool
    warnings.warn('no free sys.monitoring tool; cachify cannot track global '
                  'variables', RuntimeWarning, stacklevel=2)
    return None


# The sys.monitoring tool ID, or None if none was free
tool = _use_tool()

# Number of cached functions running
_executing = 0


def start_executing():
    """Called when a cached function starts running."""
    global _executing
    if _executing == 0 and tool is not None:
        sys.monitoring.set_events(tool, sys.monitoring.events.PY_START)
    _executing += 1


def stop_executing():
    """Called when a cached function stops running."""
    global _executing
    _executing -= 1
    if _executing == 0 and tool is not None:
        sys.monitoring.set_events(tool, 0)
