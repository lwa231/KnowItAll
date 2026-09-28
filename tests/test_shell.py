import json
import subprocess
import sys

from knowitall import shell
from knowitall.runner import Runner


def test_single_instance_blocks_a_second_holder_and_frees_on_release(tmp_path):
    lock_file = tmp_path / "app.lock"
    first, second = shell.SingleInstance(lock_file), shell.SingleInstance(lock_file)
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_single_instance_lock_dies_with_the_process(tmp_path):
    lock_file = tmp_path / "app.lock"
    code = ("import sys; sys.path.insert(0, sys.argv[1]);"
            "from knowitall.shell import SingleInstance;"
            "import pathlib; l = SingleInstance(sys.argv[2]); assert l.acquire()")
    root = str(shell.__file__).rsplit("/knowitall/", 1)[0]
    subprocess.run([sys.executable, "-c", code, root, str(lock_file)], check=True)   # exits without releasing
    again = shell.SingleInstance(lock_file)
    assert again.acquire()                     # a crashed app can never leave a stale lock
    again.release()


def test_window_state_round_trip_and_validation(tmp_path):
    path = tmp_path / "window.json"
    assert shell.load_window_state(path) == {"width": 1440, "height": 900}      # missing file
    shell.save_window_state(path, {"width": 1200, "height": 800, "x": 50, "y": 60, "junk": 1})
    assert shell.load_window_state(path) == {"width": 1200, "height": 800, "x": 50, "y": 60}


def test_window_state_accepts_the_floats_pywebview_reports(tmp_path):
    path = tmp_path / "window.json"
    shell.save_window_state(path, {"width": 1440.0, "height": 869.5, "x": 12.0, "y": 30.0})
    assert json.loads(path.read_text()) == {"width": 1440, "height": 870, "x": 12, "y": 30}   # saved as ints
    path.write_text(json.dumps({"width": 1440.0, "height": 870.0}))
    assert shell.load_window_state(path) == {"width": 1440, "height": 870}
    path.write_text(json.dumps({"width": 1500.5, "height": 870}))
    assert shell.load_window_state(path) == {"width": 1440, "height": 870}   # fractional width -> default


def test_window_state_rejects_bad_values(tmp_path):
    path = tmp_path / "window.json"
    path.write_text(json.dumps({"width": 5, "height": "tall", "x": 10}))
    assert shell.load_window_state(path) == {"width": 1440, "height": 900}      # too small / wrong type / lone x
    path.write_text("not json")
    assert shell.load_window_state(path) == {"width": 1440, "height": 900}
    path.write_text("[1, 2]")
    assert shell.load_window_state(path) == {"width": 1440, "height": 900}


def test_fits_on_screen_drops_positions_on_unplugged_monitors():
    screens = [(0, 0, 1920, 1080)]
    assert shell.fits_on_screen({"x": 100, "y": 100}, screens)
    assert not shell.fits_on_screen({"x": 2500, "y": 100}, screens)       # was on a second monitor
    assert not shell.fits_on_screen({"x": 100, "y": 100}, [])             # no screen info: don't guess
    assert not shell.fits_on_screen({}, screens)
    assert shell.fits_on_screen({"x": -1800, "y": 50}, [(-1920, 0, 1920, 1080), (0, 0, 1920, 1080)])


def test_confirm_close_only_while_scanning():
    assert shell.confirm_close_needed(True) and not shell.confirm_close_needed(False)


def test_runner_notice_reaches_snapshot_and_event_feed():
    runner = Runner()
    runner.notice("no_chrome", "warning", "Chrome missing")
    runner.notice("no_chrome", "warning", "Chrome missing")            # same code: stays one notice
    assert runner.snapshot()["notices"] == [{"code": "no_chrome", "level": "warning", "text": "Chrome missing"}]
    kinds = [e["kind"] for e in runner.events_since(0)[0]]
    assert "notice" in kinds


def test_macos_interrupt_takeover_imports_pyobjc_on_the_main_thread():
    """Regression: importing PyObjC's signal module for the first time from the helper thread that swaps the Ctrl+C
    handler stops interrupts reaching it at all (the window then ignores Ctrl+C). The imports must stay in
    _handle_interrupt itself, which runs on the main thread before the window starts."""
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).resolve().parent.parent / "ui.py").read_text(encoding="utf-8"))
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_handle_interrupt")
    helper = next(n for n in ast.walk(handler) if isinstance(n, ast.FunctionDef) and n.name == "take_over_sigint")
    assert not [n for n in ast.walk(helper) if isinstance(n, (ast.Import, ast.ImportFrom))]
    imported = {alias.name for n in ast.walk(handler) if isinstance(n, ast.ImportFrom) for alias in n.names}
    assert {"MachSignals", "_machsignals"} <= imported
