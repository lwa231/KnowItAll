"""Desktop-shell helpers that don't depend on any GUI toolkit (so they can be tested): the
single-instance lock and the remembered window geometry."""
import json
import os
import sys
from pathlib import Path

DEFAULT_SIZE = (1440, 900)
MIN_SIZE = (1000, 680)


class SingleInstance:
    """An OS file lock: released automatically if the process dies, so it can never go stale."""

    def __init__(self, path):
        self.path = Path(path)
        self._file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._file = handle
        return True

    def release(self):
        handle, self._file = self._file, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def _whole(value):
    """pywebview reports sizes as floats (1440.0); accept those, reject bools/strings/fractions."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value == int(value) else None


def load_window_state(path):
    """{'width','height'} always; 'x','y' only when they were saved. Bad files fall back to defaults."""
    state = {"width": DEFAULT_SIZE[0], "height": DEFAULT_SIZE[1]}
    try:
        saved = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return state
    if not isinstance(saved, dict):
        return state
    for key, floor in (("width", MIN_SIZE[0]), ("height", MIN_SIZE[1])):
        value = _whole(saved.get(key))
        if value is not None and floor <= value <= 10000:
            state[key] = value
    for key in ("x", "y"):
        value = _whole(saved.get(key))
        if value is not None and -10000 <= value <= 20000:
            state[key] = value
    if "x" not in state or "y" not in state:
        state.pop("x", None)
        state.pop("y", None)
    return state


def save_window_state(path, state):
    try:
        Path(path).write_text(
            json.dumps({k: round(state[k]) for k in ("width", "height", "x", "y") if k in state}),
            encoding="utf-8")
    except OSError:
        pass


def fits_on_screen(state, screens):
    """Would the window's title bar land on a connected screen? `screens` = [(x, y, width, height)].

    A position saved on a monitor that has since been unplugged must not leave the window off-screen.
    """
    if "x" not in state or "y" not in state:
        return False
    x, y = state["x"] + 40, state["y"] + 10          # a point inside the title bar
    return any(sx <= x < sx + sw and sy <= y < sy + sh for sx, sy, sw, sh in screens)


def confirm_close_needed(running):
    return bool(running)


def webview_backend():
    """Force WebView2 on Windows: pywebview would otherwise fall back to the old IE engine and
    render the UI badly, and we would rather fail with a clear message."""
    return "edgechromium" if sys.platform == "win32" else None
