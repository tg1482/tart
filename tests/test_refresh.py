import json
import os
import time

from tartifacts import manifest, refresh, status


def make(tmp_path, name="thing", **fields):
    repo = tmp_path / "repo"
    (repo / ".tart").mkdir(parents=True, exist_ok=True)
    path = repo / ".tart" / f"{name}.json"
    path.write_text(json.dumps({"title": "Thing", **fields}))
    return manifest.load(path)


TOUCH = "python3 -c \"open('out.json','w').write('{}')\""


def test_no_fetch_declared_means_nothing_to_run(tmp_path):
    keeper = refresh.Keeper(make(tmp_path, data="out.json"))
    assert keeper.can_fetch is False
    keeper.force()  # must be a no-op, not an error
    keeper.start()


def test_force_runs_fetch_even_when_data_is_fresh(tmp_path):
    # `r` should actually refetch — re-reading a stale file would make the
    # key a no-op, which is the bug this exists to fix.
    ptr = make(tmp_path, data="out.json", fetch=TOUCH, stale_after="1h")
    (tmp_path / "repo" / "out.json").write_text("{}")
    old = time.time() - 600
    os.utime(tmp_path / "repo" / "out.json", (old, old))
    assert ptr.is_stale() is False

    keeper = refresh.Keeper(ptr)
    keeper.start()
    keeper.force()
    _wait_until(lambda: ptr.data_age() < 60)
    assert ptr.data_age() < 60  # file was rewritten despite being fresh


def test_auto_refresh_fetches_missing_data(tmp_path):
    ptr = make(tmp_path, data="out.json", fetch=TOUCH, stale_after="1s", auto_refresh=True)
    assert ptr.data_age() is None  # nothing there yet

    keeper = refresh.Keeper(ptr)
    keeper._interval = lambda: 0.05
    keeper.start()
    _wait_until(lambda: ptr.data_age() is not None)
    assert (tmp_path / "repo" / "out.json").exists()


def test_auto_refresh_off_leaves_stale_data_alone(tmp_path):
    ptr = make(tmp_path, data="out.json", fetch=TOUCH, stale_after="1s")  # auto_refresh defaults off
    keeper = refresh.Keeper(ptr)
    keeper._interval = lambda: 0.05
    keeper.start()
    time.sleep(0.4)
    assert ptr.data_age() is None  # never fetched on its own


def test_interval_is_a_fraction_of_the_limit_but_floored(tmp_path):
    fast = refresh.Keeper(make(tmp_path, "fast", fetch=TOUCH, stale_after="10s"))
    assert fast._interval() == refresh.MIN_CHECK_INTERVAL  # floored, can't spin

    slow = refresh.Keeper(make(tmp_path, "slow", fetch=TOUCH, stale_after="24h"))
    assert slow._interval() == 86400 * refresh.CHECK_FRACTION


def test_failing_fetch_runs_but_does_not_raise(tmp_path):
    """A broken fetch must leave the artifact showing stale data, not die.
    Asserting only "did not raise" also passed a Keeper that never ran the
    command at all — so the evidence it RAN is what's asserted."""
    marker = tmp_path / "ran.txt"
    ptr = make(tmp_path, data="out.json", stale_after="1s", auto_refresh=True,
               fetch=f"touch {marker}; exit 3")
    keeper = refresh.Keeper(ptr)
    keeper._run()
    assert marker.exists()          # the command was actually invoked
    assert not (tmp_path / "out.json").exists()   # and it legitimately failed


def test_failing_fetch_is_recorded_not_swallowed(tmp_path):
    """The keeper's whole failure story used to be `capture_output=True`
    then nothing — a cron-style fetch failed for 15 hours and the diagnosis
    (its stderr) was destroyed each round. Now the outcome is on disk and
    on the keeper, stderr tail intact."""
    ptr = make(tmp_path, data="out.json", auto_refresh=True,
               fetch="echo diagnosing >&2; exit 127")
    keeper = refresh.Keeper(ptr)
    keeper._run()
    assert keeper.last_fetch["ok"] is False
    assert keeper.last_fetch["exit_code"] == 127
    assert "diagnosing" in keeper.last_fetch["output_tail"]

    on_disk = status.last_fetch(ptr.path)
    assert on_disk["exit_code"] == 127 and on_disk["trigger"] == "keeper"


def test_successful_fetch_clears_the_record(tmp_path):
    ptr = make(tmp_path, data="out.json", auto_refresh=True, fetch=TOUCH)
    keeper = refresh.Keeper(ptr)
    keeper._run()
    assert keeper.last_fetch["ok"] is True
    assert status.last_fetch(ptr.path)["ok"] is True


def test_wedged_fetch_is_recorded_as_a_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh, "FETCH_TIMEOUT", 0.2)
    ptr = make(tmp_path, data="out.json", auto_refresh=True, fetch="sleep 5")
    keeper = refresh.Keeper(ptr)
    keeper._run()
    assert keeper.last_fetch["ok"] is False
    assert "timed out" in keeper.last_fetch["error"]


def test_keeper_seeds_last_fetch_from_disk(tmp_path):
    """A fetch that failed BEFORE launch — cron overnight, `tart run`'s
    pre-launch refresh — must be visible from the artifact's first frame."""
    ptr = make(tmp_path, data="out.json", auto_refresh=True, fetch=TOUCH)
    status.record_fetch(ptr.path, trigger="cli", duration=0.1, exit_code=127)
    keeper = refresh.Keeper(ptr)
    assert keeper.last_fetch["exit_code"] == 127


def _wait_until(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)


def test_auto_refresh_without_stale_after_does_not_hammer_fetch(tmp_path):
    """With data present and no `stale_after`, is_stale() is None. Treating
    None as "fetch" re-ran an expensive command every 30s forever with its
    output swallowed — the bug the docstring describes, previously unpinned."""
    data = tmp_path / "d.json"
    data.write_text("{}")
    spec = tmp_path / "m.json"
    spec.write_text(json.dumps({
        "title": "T", "run": "true", "fetch": "true",
        "data": str(data), "auto_refresh": True,      # no stale_after
    }))
    keeper = refresh.Keeper(manifest.load(spec))
    assert keeper.should_fetch() is False


def test_auto_refresh_still_self_heals_missing_data(tmp_path):
    """The other half: absent data IS worth fetching, so the artifact
    repairs itself rather than sitting empty."""
    spec = tmp_path / "m.json"
    spec.write_text(json.dumps({
        "title": "T", "run": "true", "fetch": "true",
        "data": str(tmp_path / "absent.json"), "auto_refresh": True,
    }))
    keeper = refresh.Keeper(manifest.load(spec))
    assert keeper.should_fetch() is True


def test_auto_refresh_off_never_fetches_however_stale(tmp_path):
    spec = tmp_path / "m.json"
    spec.write_text(json.dumps({
        "title": "T", "run": "true", "fetch": "true",
        "data": str(tmp_path / "absent.json"), "stale_after": "1s",
    }))
    keeper = refresh.Keeper(manifest.load(spec))
    assert keeper.should_fetch() is False
