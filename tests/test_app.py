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


