import pytest

from knowitall.runner import Runner


@pytest.fixture
def runner():
    return Runner()


def test_events_since_returns_only_newer_events(runner):
    for n in range(5):
        runner.emit("log", text=str(n))
    events, cursor = runner.events_since(3)
    assert [e["text"] for e in events] == ["3", "4"]
    assert cursor == 5


def test_snapshot_lists_companies_in_queue_order(runner):
    runner.queue(["b.com", "a.com", "b.com", "not a domain"])
    assert [c["domain"] for c in runner.snapshot()["companies"]] == ["b.com", "a.com"]


def test_ids_stay_unique_and_cursor_never_moves_backwards_after_trim(runner):
    cursors = []
    for n in range(20_500):
        runner.emit("log", text=str(n))
        if n % 500 == 0:
            cursors.append(runner.events_since(0)[1])
    events, cursor = runner.events_since(0)
    ids = [e["i"] for e in events]
    assert len(ids) == len(set(ids))
    assert cursors == sorted(cursors) and cursor >= cursors[-1]
    # A client that polled just before the trim must not be handed old events again.
    replay, _ = runner.events_since(20_000)
    assert all(e["i"] >= 20_000 for e in replay)
    assert len(replay) <= 501
