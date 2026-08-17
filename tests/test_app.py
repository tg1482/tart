"""app.run() wiring: what the manifest supplies, and cursor auto-handling."""

import json

from tartifacts import app, widgets


def manifest(tmp_path, **fields):
    repo = tmp_path / "repo"
    (repo / ".tart").mkdir(parents=True)
    (repo / "data").mkdir()
    (repo / "data" / "d.json").write_text('{"n": 1}')
    path = repo / ".tart" / "thing.json"
    path.write_text(json.dumps({"title": "From manifest", "run": "true", **fields}))
    return path


def test_source_and_title_come_from_the_manifest(tmp_path, monkeypatch, capsys):
    # The point of the change: the artifact script no longer repeats the data
    # path (writer + reader + manifest was three copies, none checked).
    monkeypatch.setenv("TART_MANIFEST", str(manifest(tmp_path, data="data/d.json")))
    captured = {}

    def render(state, console):
        captured["state"] = state
        return "x"

    app.run(render=render, argv=["--json"])
    out = json.loads(capsys.readouterr().out)
    assert out == {"data": {"n": 1}}          # landed under "data", unasked


def test_cursor_is_auto_handled_when_rows_given():
    state = {"cursor": widgets.Cursor()}
    dispatch = app._keys(rows=lambda st: list(range(10)), on_key=None)
    dispatch("j", state)
    dispatch("j", state)
    assert state["cursor"].index == 2


def test_keys_the_cursor_declines_reach_on_key():
    state, seen = {"cursor": widgets.Cursor()}, []
    dispatch = app._keys(rows=lambda st: list(range(10)), on_key=lambda k, s: seen.append(k))
    dispatch("j", state)      # cursor takes it
    dispatch("d", state)      # cursor declines
    assert seen == ["d"] and state["cursor"].index == 1


def test_without_rows_every_key_reaches_on_key():
    seen = []
    dispatch = app._keys(rows=None, on_key=lambda k, s: seen.append(k))
    dispatch("j", {"cursor": widgets.Cursor()})
    assert seen == ["j"]


# --- mode-aware dispatch ---------------------------------------------------




# --- the warning bar and source diagnosis -----------------------------------


def test_file_source_problem_distinguishes_the_three_nones(tmp_path):
    """read_now() collapses missing, corrupt and unreadable into one None;
    problem() is what tells an agent which it was."""
    missing = app.FileSource(tmp_path / "absent.json")
    assert "does not exist" in missing.problem()

    corrupt = tmp_path / "torn.json"
    corrupt.write_text('{"half": ')
    assert "not valid JSON" in app.FileSource(corrupt).problem()

    fine = tmp_path / "ok.json"
    fine.write_text("{}")
    assert app.FileSource(fine).problem() is None


def test_warning_prefers_broken_data_over_failed_fetch(tmp_path):
    """On-screen-not-matching-disk beats won't-get-fresher."""
    class FakeKeeper:
        last_fetch = {"ok": False, "exit_code": 7, "at": 0}

    said = app._warning("Expecting value: line 1", FakeKeeper(), "spend")
    assert "data file unreadable" in said

    said = app._warning(None, FakeKeeper(), "spend")
    assert "fetch failed (exit 7" in said
    assert "tart logs spend" in said       # where to look next, named


def test_no_warning_when_healthy():
    class FakeKeeper:
        last_fetch = {"ok": True, "exit_code": 0, "at": 0}

    assert app._warning(None, FakeKeeper(), "spend") is None
    assert app._warning(None, None, None) is None   # manifest-less artifact
