"""The audit hook.

Files opened for reading and directories that are listed become inputs.
Other operations on outside state issue an UntrackedEffectWarning.
"""

import os
import sys
import warnings
from contextvars import ContextVar

from . import _state
from ._errors import UntrackedEffectWarning
from ._inputs import track_path

# Audit events for operations that read or change state outside the program.
_EVENT_KINDS = dict.fromkeys([
    # Files and directories
    'glob.glob', 'pathlib.Path.glob', 'pathlib.Path.rglob',
    'os.getxattr', 'os.listxattr', 'os.setxattr', 'os.removexattr',
    'os.mkdir', 'os.rmdir', 'os.remove', 'os.rename', 'os.link',
    'os.symlink', 'os.truncate', 'os.utime', 'os.chmod', 'os.chown',
    'os.chflags', 'os.chdir', 'shutil.copyfile', 'shutil.copymode',
    'shutil.copystat', 'shutil.copytree', 'shutil.move', 'shutil.rmtree',
    'shutil.chown', 'shutil.make_archive', 'shutil.unpack_archive',
    'tempfile.mkstemp', 'tempfile.mkdtemp',
    # Environment variables
    'os.putenv', 'os.unsetenv', 'os._clearenv',
    # Processes
    'subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn',
    'os.spawn', 'os.fork', 'os.forkpty', 'os.startfile', 'os.kill',
    'os.killpg', 'pty.spawn',
    # Network
    'socket.connect', 'socket.bind', 'socket.sendto', 'socket.sendmsg',
    'socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr',
    'socket.gethostname', 'socket.getnameinfo', 'http.client.connect',
    'urllib.Request', 'ftplib.connect', 'smtplib.connect', 'poplib.connect',
    'imaplib.open', 'webbrowser.open',
    # Interaction
    'builtins.input',
], 'untracked')
_EVENT_KINDS.update({'open': 'open', 'os.listdir': 'dir', 'os.scandir': 'dir'})

_WRITE_FLAGS = (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT
                | os.O_TRUNC | os.O_EXCL)

_in_audit_hook = ContextVar('cachify_in_audit_hook', default=False)


# Module globals are bound as defaults so the hook keeps working while the
# interpreter shuts down.
def _audit_hook(event, args, *,
                kinds=_EVENT_KINDS, active=_state.active,
                in_hook=_in_audit_hook, getframe=sys._getframe,
                warn=warnings.warn, fspath=os.fspath, PathLike=os.PathLike,
                write_flags=_WRITE_FLAGS, track_path=track_path,
                skip=_state.PACKAGE_DIRS):
    kind = kinds.get(event)
    if kind is None:
        return
    memo = active.get()
    if memo is None or in_hook.get():
        return

    # Reading source and bytecode files while importing is not an effect of
    # the cached function.
    frame = getframe(1)
    while frame is not None:
        if frame.f_code.co_filename.startswith('<frozen importlib.'):
            return
        frame = frame.f_back

    target = args[0] if args else None
    if kind == 'open':
        flags = args[2] if len(args) > 2 else None
        if (isinstance(flags, int) and not flags & write_flags
                and track_path(target)):
            return
    elif kind == 'dir':
        if track_path('.' if target is None else target):
            return

    name = getattr(memo.query.user_function, '__qualname__', '<function>')
    if isinstance(target, PathLike):
        try:
            target = fspath(target)
        except Exception:
            target = None
    detail = f' on {target!r}' if isinstance(target, (str, bytes)) else ''

    # Showing a warning can open files too (for example to read the source
    # line), which must not trigger the hook again.
    token = in_hook.set(True)
    try:
        warn(f"cached function {name}() used {event!r}{detail}, which "
             f"cachify does not track; its cached result may go out of date "
             f"or skip this effect",
             UntrackedEffectWarning, skip_file_prefixes=skip)
    finally:
        in_hook.reset(token)


sys.addaudithook(_audit_hook)
