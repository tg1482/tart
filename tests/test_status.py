"""The flight recorder. The property that matters most: recording never
raises and reading never lies — a fetch that failed at 3am must still be
explainable at 9am, from the file alone."""

import json
import time

from tartifacts import status


def spec(tmp_path, name="thing"):
    path = tmp_path / ".tart" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"title": "T", "run": "true"}))
    return path


def test_roundtrip_failure(tmp_path):
    path = spec(tmp_path)
    status.record_fetch(path, trigger="keeper", duration=0.31, exit_code=127,
                        output="sh: uv: command not found\n")
    last = status.last_fetch(path)
    assert last["ok"] is False
    assert last["exit_code"] == 127
    assert last["trigger"] == "keeper"
    assert "uv: command not found" in last["output_tail"]
    assert status.describe(last) == "exit 127"


def test_roundtrip_success_and_error(tmp_path):
    path = spec(tmp_path)
    status.record_fetch(path, trigger="cli", duration=1.0, exit_code=0, output="fetched\n")
    assert status.last_fetch(path)["ok"] is True

    # An error (timeout, spawn failure) has no exit code — still not ok.
    status.record_fetch(path, trigger="cli", duration=600.0, error="timed out after 600s")
    last = status.last_fetch(path)
    assert last["ok"] is False and last["exit_code"] is None
    assert status.describe(last) == "timed out after 600s"


def test_no_record_reads_as_none(tmp_path):
    assert status.last_fetch(spec(tmp_path)) is None
    assert status.log_tail(spec(tmp_path)) == ""


def test_same_name_different_repos_do_not_share_a_record(tmp_path):
    a = spec(tmp_path / "repo-a")
    b = spec(tmp_path / "repo-b")
    status.record_fetch(a, trigger="cli", duration=0.1, exit_code=0)
    status.record_fetch(b, trigger="cli", duration=0.1, exit_code=3)
    assert status.last_fetch(a)["ok"] is True
    assert status.last_fetch(b)["ok"] is False


def test_log_accumulates_and_shows_output(tmp_path):
    path = spec(tmp_path)
    status.record_fetch(path, trigger="keeper", duration=0.1, exit_code=1, output="first\n")
    status.record_fetch(path, trigger="keeper", duration=0.1, exit_code=1, output="second\n")
    log = status.log_tail(path)
    assert "first" in log and "second" in log
    assert "FAILED (exit 1)" in log


def test_log_is_capped(tmp_path):
    """A fetch failing every 30s for a week must not grow the log without
    bound; the cut resumes at an entry boundary so the tail stays parseable."""
    path = spec(tmp_path)
    noise = "x" * status.TAIL_CHARS
    for _ in range(status.LOG_CAP // status.TAIL_CHARS + 10):
        status.record_fetch(path, trigger="keeper", duration=0.1, exit_code=1, output=noise)
    assert status.log_path(path).stat().st_size <= status.LOG_CAP
    assert status.log_tail(path, lines=10_000).startswith("── ")


def test_output_tail_is_bounded(tmp_path):
    path = spec(tmp_path)
    status.record_fetch(path, trigger="cli", duration=0.1, exit_code=1,
                        output="a" * (status.TAIL_CHARS * 3) + "END")
    tail = status.last_fetch(path)["output_tail"]
    assert len(tail) <= status.TAIL_CHARS
    assert tail.endswith("END")  # the tail, not the head — the diagnosis is at the end


def test_recording_survives_an_unwritable_home(tmp_path, monkeypatch):
    """Observability must not take down the thing it observes."""
    from tartifacts import paths
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the directory should be")
    monkeypatch.setenv(paths.ENV_VAR, str(blocked))
    entry = status.record_fetch(spec(tmp_path), trigger="keeper", duration=0.1, exit_code=1)
    assert entry["ok"] is False  # the outcome is still returned to the caller


def test_recent_timestamp(tmp_path):
    path = spec(tmp_path)
    status.record_fetch(path, trigger="cli", duration=0.1, exit_code=0)
    assert abs(time.time() - status.last_fetch(path)["at"]) < 5
