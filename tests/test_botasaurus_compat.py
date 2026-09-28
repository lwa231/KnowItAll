"""Pins the botasaurus behaviour this app leans on (L1); a version bump that breaks these needs a re-test."""
import importlib.metadata

from knowitall import fetch


def test_version_is_the_pinned_one():
    assert importlib.metadata.version("botasaurus") == "4.0.97"


def test_retry_on_network_error_is_neutralised():
    from botasaurus_requests import reqs
    from knowitall import compat
    compat.apply()
    assert reqs.retry_on_network_error(lambda: "once") == "once"     # no infinite retry loop


def test_driver_exposes_the_private_apis_browser_py_uses():
    from botasaurus_driver import Driver
    for attribute in ("open_link_in_new_tab", "run_js", "get"):
        assert hasattr(Driver, attribute), attribute
    assert "_tab" in Driver.__init__.__code__.co_names or hasattr(Driver, "current_url")


def test_cache_directory_setter_takes_absolute_paths(tmp_path):
    from botasaurus.cache import Cache
    original = Cache.cache_directory
    try:
        Cache.set_cache_directory(tmp_path / "c")
        assert Cache.cache_directory.startswith(str(tmp_path))
    finally:
        Cache.cache_directory = original
