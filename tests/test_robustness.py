"""Failure paths, written before their fixes.

Every one of these is a defect an adversarial reviewer found in code that
had passing tests: the guard existed, but it guarded the seam I was
thinking about rather than the one the code uses.
"""

import json
import os


from tartifacts import cli, manifest


# --- a summary must not kill a live artifact -------------------------------


def test_a_fetch_that_does_not_rewrite_the_data_file_is_caught(tmp_path):
    """The closed loop the existence check missed: with a stale file left
    from a previous run, a fetch that writes elsewhere passed the old
    `not data_path.exists()` guard forever after the first run."""
    from tartifacts import trust

    (tmp_path / ".tart").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "d.json").write_text('{"old": true}')
    # backdate it so the fetch (which touches nothing) can't tie its mtime
    old = (tmp_path / "data" / "d.json").stat().st_mtime - 100
    os.utime(tmp_path / "data" / "d.json", (old, old))
    spec = tmp_path / ".tart" / "f.json"
    spec.write_text(json.dumps({"title": "F", "run": "true", "data": "data/d.json",
                                "fetch": "true"}))       # writes nothing
    trust.trust(spec)
    assert cli._fetch(manifest.load(spec)) == 1


def test_a_fetch_that_rewrites_the_data_file_succeeds(tmp_path):
    from tartifacts import trust

    (tmp_path / ".tart").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "d.json").write_text('{"old": true}')
    old = (tmp_path / "data" / "d.json").stat().st_mtime - 100
    os.utime(tmp_path / "data" / "d.json", (old, old))
    spec = tmp_path / ".tart" / "f.json"
    spec.write_text(json.dumps({"title": "F", "run": "true", "data": "data/d.json",
                                "fetch": "echo '{\"new\": true}' > data/d.json"}))
    trust.trust(spec)
    assert cli._fetch(manifest.load(spec)) == 0


def test_a_signal_killed_fetch_reports_128_plus_signum(tmp_path):
    """subprocess reports -N; sys.exit(-N) truncates to a status matching no
    runbook. cron and CI expect 128+N (137 for SIGKILL)."""
    from tartifacts import trust

    (tmp_path / ".tart").mkdir()
    spec = tmp_path / ".tart" / "s.json"
    spec.write_text(json.dumps({"title": "S", "run": "true", "fetch": "kill -9 $$"}))
    trust.trust(spec)
    assert cli._fetch(manifest.load(spec)) == 137
