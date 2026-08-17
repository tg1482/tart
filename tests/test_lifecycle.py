"""Process lifecycle: what happens to children when tart stops wanting
them, and what `tart run` decides to refetch. The through-line: a process
tart started must never outlive tart's interest in it, and liveness
claims must be checkable."""

import json
import time

from tartifacts import app, manifest, refresh, registry
from tartifacts.cli import _needs_fetch


def make(tmp_path, **fields):
    repo = tmp_path / "repo"
    (repo / ".tart").mkdir(parents=True, exist_ok=True)
    path = repo / ".tart" / "thing.json"
    path.write_text(json.dumps({"title": "Thing", **fields}))
    return manifest.load(path)


# --- timeouts kill the process GROUP, not just sh ---------------------------


def test_keeper_timeout_kills_the_grandchild_too(tmp_path, monkeypatch):
    """Killing only `sh` on timeout left the real fetch alive — it kept
    running (and could rewrite the data file) minutes after tart reported
    a timeout. The backgrounded touch below is that grandchild: if the
    group dies, its marker never appears."""
    monkeypatch.setattr(refresh, "FETCH_TIMEOUT", 0.3)
    escaped = tmp_path / "escaped.txt"
    ptr = make(tmp_path, data="out.json", auto_refresh=True,
               fetch=f"(sleep 1; touch {escaped}) & sleep 30")
    keeper = refresh.Keeper(ptr)
    started = time.time()
    keeper._run()
    assert time.time() - started < 5          # communicate() returned promptly
    assert "timed out" in keeper.last_fetch["error"]
    time.sleep(1.2)                           # past when the grandchild would fire
    assert not escaped.exists()


# --- keeper shutdown --------------------------------------------------------


def test_stop_kills_an_in_flight_fetch(tmp_path):
    ptr = make(tmp_path, data="out.json", auto_refresh=True, fetch="sleep 30")
    keeper = refresh.Keeper(ptr)
    keeper.start()
    keeper.force()
    deadline = time.time() + 3
    while keeper._proc is None and time.time() < deadline:
        time.sleep(0.02)
    proc = keeper._proc
    assert proc is not None                   # the fetch really was in flight

    keeper.stop()
    deadline = time.time() + 3
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.02)
    assert proc.poll() is not None            # dead, not orphaned
    # A cancelled fetch is not a failed one: nothing was recorded for it.
    assert keeper.last_fetch is None


def test_stop_before_any_fetch_is_a_quiet_no_op(tmp_path):
    keeper = refresh.Keeper(make(tmp_path, data="out.json", fetch="true"))
    keeper.stop()


# --- what `tart run` refetches ----------------------------------------------


def test_present_data_with_no_stale_after_is_not_refetched(tmp_path):
    """is_stale() is None here, and treating unjudgeable as "fetch" re-ran
    a potentially expensive command on every launch — the CLI twin of the
    keeper bug already pinned in test_refresh."""
    ptr = make(tmp_path, data="out.json", fetch="true")
    (tmp_path / "repo" / "out.json").write_text("{}")
    assert _needs_fetch(ptr) is False


def test_missing_or_stale_data_is_refetched(tmp_path):
    ptr = make(tmp_path, data="out.json", fetch="true", stale_after="1h")
    assert _needs_fetch(ptr) is True          # missing

    data = tmp_path / "repo" / "out.json"
    data.write_text("{}")
    assert _needs_fetch(ptr) is False         # fresh

    import os
    old = time.time() - 7200
    os.utime(data, (old, old))
    assert _needs_fetch(ptr) is True          # stale


def test_no_data_declared_still_fetches(tmp_path):
    # Nothing to judge by; the declared fetch is all we have.
    assert _needs_fetch(make(tmp_path, fetch="true")) is True


# --- restart on code change -------------------------------------------------


def test_code_files_watches_the_run_scripts(tmp_path):
    ptr = make(tmp_path, run="python3 show.py --flag x", fetch="python3 fetch.py")
    show = tmp_path / "repo" / "show.py"
    show.write_text("print('hi')\n")
    watched = app._code_files(ptr)
    assert list(watched) == [str(show)]       # fetch.py spawns fresh; not watched
    assert watched[str(show)] is not None


def test_code_change_is_detected_once_the_file_settles(tmp_path):
    show = tmp_path / "repo" / "show.py"
    ptr = make(tmp_path, run="python3 show.py")
    show.write_text("v1\n")
    watched = app._code_files(ptr)
    assert app._code_changed(watched) is False

    time.sleep(0.01)
    show.write_text("v2\n")
    import os
    os.utime(show, (time.time() + 5, time.time() + 5))  # force a distinct mtime
    assert app._code_changed(watched) is True
    assert app._code_changed(watched) is False          # baseline moved with it


def test_a_vanished_script_does_not_trigger_a_restart(tmp_path):
    """Restarting into an editor's write-rename gap would exec a script
    that isn't there; a deleted script must wait until it comes back."""
    show = tmp_path / "repo" / "show.py"
    ptr = make(tmp_path, run="python3 show.py")
    show.write_text("v1\n")
    watched = app._code_files(ptr)
    show.unlink()
    assert app._code_changed(watched) is False


def test_artifact_without_a_manifest_watches_nothing():
    assert app._code_files(None) == {}


# --- pid reuse --------------------------------------------------------------


def test_parse_etime_all_four_shapes():
    assert registry._parse_etime("42") == 42
    assert registry._parse_etime("05:07") == 307
    assert registry._parse_etime("02:00:01") == 7201
    assert registry._parse_etime("3-01:00:00") == 3 * 86400 + 3600
    assert registry._parse_etime("garbage") is None


def test_a_reused_pid_is_evicted(monkeypatch, tmp_path):
    """Signal 0 says A process exists, not that it's OURS: an artifact
    killed -9 whose pid the OS re-issued read as [live] forever."""
    registry.live_dir().mkdir(parents=True)
    entry = {"pid": 999999, "manifest": str(tmp_path / "m.json"), "title": "T",
             "started_at": time.time() - 50_000, "pane": None, "tty": None}
    (registry.live_dir() / "999999.json").write_text(json.dumps(entry))

    monkeypatch.setattr(registry, "_alive", lambda pid: True)   # impostor exists
    monkeypatch.setattr(registry, "_parse_etime", lambda text: 60.0)  # started 1m ago
    monkeypatch.setattr(registry.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "01:00"})())
    assert registry.live() == []
    assert not (registry.live_dir() / "999999.json").exists()


def test_our_own_process_stays_live(tmp_path):
    import os
    registry.register(str(tmp_path / "m.json"), "Me")
    try:
        assert os.getpid() in [e.pid for e in registry.live()]
    finally:
        registry.unregister()


# --- failing-since ----------------------------------------------------------


def test_last_ok_survives_failures(tmp_path):
    """"exit 1, 21m ago" describes the newest attempt; the question that
    matters is "how long has this been broken". An artifact retried every
    6 minutes for four days and each failure looked 6 minutes old."""
    from tartifacts import status
    spec = make(tmp_path, fetch="true").path
    status.record_fetch(spec, trigger="cli", duration=0.1, exit_code=0)
    status.record_fetch(spec, trigger="keeper", duration=0.1, exit_code=1)
    status.record_fetch(spec, trigger="keeper", duration=0.1, exit_code=1)
    last = status.last_fetch(spec)
    assert last["last_ok"] is not None
    assert status.failing_for(last) is not None
    assert status.failing_for(last) < 60          # the success was seconds ago

    status.record_fetch(spec, trigger="cli", duration=0.1, exit_code=0)
    assert status.failing_for(status.last_fetch(spec)) is None   # healthy again


def test_never_succeeded_has_no_failing_duration(tmp_path):
    from tartifacts import status
    spec = make(tmp_path, fetch="true").path
    status.record_fetch(spec, trigger="cli", duration=0.1, exit_code=1)
    assert status.failing_for(status.last_fetch(spec)) is None


# --- tart restart -----------------------------------------------------------


def test_restart_signals_restartable_entries_only(monkeypatch, tmp_path, capsys):
    from tartifacts import cli
    new = registry.Entry(pid=111, manifest=str(tmp_path / "a.json"), title="New",
                         started_at=0, restartable=True)
    old = registry.Entry(pid=222, manifest=str(tmp_path / "b.json"), title="Old",
                         started_at=0)                     # pre-restart entry
    monkeypatch.setattr(registry, "live", lambda: [new, old])
    signalled = []
    monkeypatch.setattr(cli.os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    cli._restart(["--all"])
    out = capsys.readouterr().out
    assert signalled == [(111, cli.signal.SIGUSR1)]        # Old was NOT signalled
    assert "restarting New" in out
    assert "launched before restart support" in out        # ...and told why


def test_restart_by_name_matches_the_manifest(monkeypatch, tmp_path, capsys):
    from tartifacts import cli
    ptr = make(tmp_path, run="true")
    mine = registry.Entry(pid=111, manifest=str(ptr.path.resolve()), title="Mine",
                          started_at=0, restartable=True)
    other = registry.Entry(pid=222, manifest=str(tmp_path / "other.json"),
                           title="Other", started_at=0, restartable=True)
    monkeypatch.setattr(registry, "live", lambda: [mine, other])
    monkeypatch.setattr(cli, "_resolve_or_exit", lambda name: ptr)
    signalled = []
    monkeypatch.setattr(cli.os, "kill", lambda pid, sig: signalled.append(pid))

    cli._restart(["thing"])
    assert signalled == [111]


# --- cron --sync block management -------------------------------------------


def test_managed_block_preserves_user_lines_and_replaces_itself():
    from tartifacts import cli
    user = "PATH=/usr/bin\n0 3 * * * backup.sh\n"
    once = cli._with_managed_block(user, ["0 * * * * tart fetch a"])
    twice = cli._with_managed_block(once, ["30 * * * * tart fetch b"])

    assert "backup.sh" in twice
    assert "tart fetch b" in twice
    assert "tart fetch a" not in twice          # replaced, not accumulated
    assert twice.count(cli.CRON_BEGIN) == 1


def test_empty_line_list_removes_the_block_entirely():
    from tartifacts import cli
    with_block = cli._with_managed_block("0 3 * * * backup.sh\n", ["* * * * * x"])
    cleared = cli._with_managed_block(with_block, [])
    assert cli.CRON_BEGIN not in cleared
    assert "backup.sh" in cleared


def test_cron_schedule_staggers_by_name():
    from tartifacts import cli
    a = cli._cron_schedule(3600, "ads")
    b = cli._cron_schedule(3600, "dash")
    assert a.endswith("*/1 * * *") and b.endswith("*/1 * * *")
    assert a.split()[0] != b.split()[0]         # different minute offsets
