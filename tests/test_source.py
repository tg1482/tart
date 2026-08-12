"""The polling engine — how an artifact notices its data changed.

Timing-sensitive by nature, so these poll for an outcome with a deadline
rather than sleeping a fixed amount and hoping.
"""

import time
from queue import Empty, Queue

import pytest

from tartifacts import jsonfile, source


def drain(q, timeout=3.0):
    """Next event within `timeout`, or None. Polls rather than blocking so
    a hung source fails the test instead of hanging the suite."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return q.get_nowait()
        except Empty:
            time.sleep(0.02)
    return None


@pytest.fixture
def data_file(tmp_path):
    path = tmp_path / "d.json"
    jsonfile.write(path, {"n": 1})            # atomic, as write_data is
    return path


def test_watch_file_delivers_current_contents(data_file):
    q = Queue()
    source.watch_file(str(data_file), q, poll_interval=0.05)
    event = drain(q)
    assert event is not None and event.ok and event.value == {"n": 1}


def test_watch_file_pushes_when_the_file_changes(data_file):
    q = Queue()
    source.watch_file(str(data_file), q, poll_interval=0.05)
    assert drain(q).value == {"n": 1}

    time.sleep(0.05)  # ensure a distinct mtime
    # Atomically, the way `tartifacts.write_data` does it. A plain
    # write_text is visible half-written, and the poller then reads a
    # truncated file — this test failed on CI for exactly that reason,
    # which is the race write_data exists to remove. Whether a torn read
    # degrades gracefully is a separate question, tested below.
    jsonfile.write(data_file, {"n": 2})
    event = drain(q)
    assert event is not None and event.value == {"n": 2}


def test_watch_file_is_quiet_when_nothing_changes(data_file):
    q = Queue()
    source.watch_file(str(data_file), q, poll_interval=0.05)
    drain(q)                      # the initial read
    assert drain(q, timeout=0.4) is None   # no spurious re-delivery


def test_trigger_forces_an_immediate_recheck(data_file):
    # The 'r' key path: don't wait out a long poll interval.
    q = Queue()
    trigger = source.watch_file(str(data_file), q, poll_interval=30.0)
    assert drain(q).value == {"n": 1}

    time.sleep(0.05)
    jsonfile.write(data_file, {"n": 3})       # atomic, as write_data is
    trigger.set()
    event = drain(q, timeout=2.0)
    assert event is not None and event.value == {"n": 3}


def test_malformed_json_reports_an_error_rather_than_crashing(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    q = Queue()
    source.watch_file(str(bad), q, poll_interval=0.05)
    event = drain(q)
    assert event is not None and event.ok is False and isinstance(event.value, str)


def test_missing_file_is_silent_until_it_appears(tmp_path):
    later = tmp_path / "later.json"
    q = Queue()
    source.watch_file(str(later), q, poll_interval=0.05)
    assert drain(q, timeout=0.3) is None      # nothing to report yet

    jsonfile.write(later, {"n": 9})           # atomic, as write_data is
    event = drain(q)
    assert event is not None and event.value == {"n": 9}


