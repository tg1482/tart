"""Liveness is checked, not asserted.

The mechanism this replaced tagged a herdr/tmux pane and trusted the tag.
That meant no discovery at all outside those two programs, and inside
them a `kill -9` left the tag behind — `tart list` would report an
artifact live in a pane where nothing was running.
"""

import json
import os

from tartifacts import registry

# The live dir is isolated by conftest's TART_HOME, so no fixture here.


def write_entry(pid, manifest="/x/.tart/a.json", title="A", pane=None):
    registry.live_dir().mkdir(parents=True, exist_ok=True)
    (registry.live_dir() / f"{pid}.json").write_text(json.dumps({
        "pid": pid, "manifest": manifest, "title": title,
        "started_at": 0.0, "pane": pane, "tty": None,
    }))


def test_registers_and_unregisters_this_process():
    registry.register("/x/.tart/a.json", "A")
    assert [e.pid for e in registry.live()] == [os.getpid()]
    registry.unregister()
    assert registry.live() == []


def test_dead_process_is_not_reported_live():
    # The kill -9 case: an entry survives, the process doesn't.
    write_entry(pid=999999)          # a pid that cannot be running
    assert registry.live() == []


def test_dead_entries_are_cleaned_up_on_read():
    write_entry(pid=999999)
    registry.live()
    assert list(registry.live_dir().glob("*.json")) == []   # self-healing


def test_pane_is_recorded_when_present_but_not_required():
    registry.register("/x/.tart/a.json", "A", pane="wA:pG")
    assert registry.live()[0].where == "wA:pG"
    registry.unregister()

    registry.register("/x/.tart/a.json", "A", pane=None)
    entry = registry.live()[0]
    assert entry.pane is None
    assert entry.where          # still answers "where", via tty or pid
    registry.unregister()


def test_corrupt_entry_is_discarded_not_fatal():
    registry.live_dir().mkdir(parents=True, exist_ok=True)
    (registry.live_dir() / "bad.json").write_text("{not json")
    assert registry.live() == []


def test_missing_live_dir_is_not_an_error():
    assert registry.live() == []


def test_a_dashboard_owned_by_another_user_stays_live(monkeypatch):
    """`os.kill(pid, 0)` raises PermissionError when the process exists but
    belongs to someone else — a root-owned or another user's dashboard on a
    shared box. Treating that as dead evicts a RUNNING artifact from `tart
    list` and deletes its entry, so it never comes back."""
    def not_yours(pid, sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "kill", not_yours)
    write_entry(pid=4242)
    assert [e.pid for e in registry.live()] == [4242]
    assert (registry.live_dir() / "4242.json").exists()   # not evicted


def test_a_process_that_is_really_gone_is_still_evicted(monkeypatch):
    """The other side of the same branch: ProcessLookupError means gone."""
    def no_such(pid, sig):
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(os, "kill", no_such)
    write_entry(pid=4242)
    assert registry.live() == []
    assert not (registry.live_dir() / "4242.json").exists()
