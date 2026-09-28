import os
import subprocess
import sys
from pathlib import Path

from knowitall import paths

ROOT = Path(__file__).resolve().parent.parent


def make_legacy(folder):
    (folder / "cache" / "_fetch_page").mkdir(parents=True)
    (folder / "cache" / "_fetch_page" / "a.json").write_text("{}")
    (folder / "output").mkdir()
    (folder / "output" / "jobs_x.com.json").write_text("[]")
    (folder / "output" / "jobs_x.com.csv").write_text("a\n")
    (folder / "output" / "notes.txt").write_text("keep me")
    for suffix in ("", "-wal", "-shm"):
        (folder / f"history.db{suffix}").write_text(f"db{suffix}")


def test_migrate_moves_db_triplet_cache_and_exports(tmp_path):
    legacy, data, exports = tmp_path / "code", tmp_path / "data", tmp_path / "exports"
    legacy.mkdir()
    make_legacy(legacy)
    moved = paths.migrate_legacy(legacy, data, exports)
    assert len(moved) == 6
    assert [p.name for p in sorted(data.glob("history.db*"))] == ["history.db", "history.db-shm", "history.db-wal"]
    assert (data / "cache" / "_fetch_page" / "a.json").exists()
    assert (exports / "jobs_x.com.json").exists() and (exports / "jobs_x.com.csv").exists()
    assert not (legacy / "history.db").exists() and not (legacy / "cache").exists()
    assert (legacy / "output" / "notes.txt").exists()      # unrelated files are never touched


def test_migrate_never_overwrites_existing_data(tmp_path):
    legacy, data, exports = tmp_path / "code", tmp_path / "data", tmp_path / "exports"
    legacy.mkdir()
    make_legacy(legacy)
    data.mkdir()
    (data / "history.db").write_text("new")
    exports.mkdir()
    (exports / "jobs_x.com.json").write_text("new")
    paths.migrate_legacy(legacy, data, exports)
    assert (data / "history.db").read_text() == "new"
    assert (legacy / "history.db").exists()                # left in place, not clobbered
    assert (exports / "jobs_x.com.json").read_text() == "new"


def test_migrate_with_nothing_to_move(tmp_path):
    assert paths.migrate_legacy(tmp_path / "missing", tmp_path / "d", tmp_path / "e") == []


def test_knowitall_home_relocates_everything_and_cwd_follows(tmp_path):
    """Fresh interpreter, launched from an unrelated directory."""
    home = tmp_path / "home"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    code = (
        "import os, sys; sys.path.insert(0, sys.argv[1]);"
        "from knowitall import paths, store; paths.enter_data_dir();"
        "store.init(); print(os.getcwd()); print(store.DB_PATH); print(paths.EXPORTS_DIR)"
    )
    env = {**os.environ, "KNOWITALL_HOME": str(home), "KNOWITALL_LEGACY_DIR": str(tmp_path / "none")}
    env.pop("KNOWITALL_EXPORTS", None)
    out = subprocess.run([sys.executable, "-c", code, str(ROOT)], cwd=elsewhere, env=env,
                         capture_output=True, text=True, check=True).stdout.split("\n")
    assert Path(out[0]).resolve() == home.resolve()
    assert Path(out[1]).resolve() == (home / "history.db").resolve()
    assert Path(out[2]).resolve() == (home / "exports").resolve()
    assert (home / "history.db").exists() and (home / "logs").is_dir() and (home / "cache").is_dir()
    assert list(elsewhere.iterdir()) == []                 # nothing leaked into the launch directory


def test_enter_data_dir_migrates_old_layout_end_to_end(tmp_path):
    """The cache folder is created by enter_data_dir, so it must not block moving the old one."""
    legacy, home = tmp_path / "code", tmp_path / "home"
    legacy.mkdir()
    make_legacy(legacy)
    code = ("import sys; sys.path.insert(0, sys.argv[1]);"
            "from knowitall import paths; paths.enter_data_dir()")
    env = {**os.environ, "KNOWITALL_HOME": str(home), "KNOWITALL_LEGACY_DIR": str(legacy)}
    env.pop("KNOWITALL_EXPORTS", None)
    subprocess.run([sys.executable, "-c", code, str(ROOT)], cwd=tmp_path, env=env, check=True, capture_output=True)
    assert (home / "history.db").exists()
    assert (home / "cache" / "_fetch_page" / "a.json").exists()
    assert (home / "exports" / "jobs_x.com.csv").exists()
    assert not (legacy / "history.db").exists()
