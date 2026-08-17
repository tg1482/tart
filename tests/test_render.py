"""The headless render pipeline — how an agent sees what an artifact shows
without a terminal. This is the capability the whole project exists for,
so it gets exercised end-to-end rather than by inspecting internals.
"""

import json

import pytest
from rich.panel import Panel
from rich.text import Text

from tartifacts import app, widgets


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    """A manifest with real data, and cwd somewhere else entirely."""
    repo = tmp_path / "repo"
    (repo / ".tart").mkdir(parents=True)
    (repo / "data").mkdir()
    (repo / "data" / "d.json").write_text(json.dumps({"rows": [{"n": 1}, {"n": 2}, {"n": 3}]}))
    manifest = repo / ".tart" / "thing.json"
    manifest.write_text(json.dumps({"title": "From manifest", "run": "true", "data": "data/d.json"}))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("TART_MANIFEST", str(manifest))
    return manifest


def rows(state):
    return (state.get("data") or {}).get("rows", [])


def render(state, console):
    return widgets.stack(
        widgets.header(f"{len(rows(state))} rows"),
        Panel(Text("BODY-MARKER"), title="Body"),
    )


def test_once_prints_a_frame_and_exits(artifact, capsys):
    app.run(render=render, argv=["--once"])
    out = capsys.readouterr().out
    assert "3 rows" in out          # data came from the manifest, unasked
    assert "BODY-MARKER" in out     # the whole renderable was printed


def test_once_respects_width(artifact, capsys):
    app.run(render=render, argv=["--once", "--width", "40"])
    widest = max(len(line) for line in capsys.readouterr().out.splitlines())
    assert widest <= 40


def test_json_prints_the_summary(artifact, capsys):
    app.run(render=render, summary=lambda st: {"count": len(rows(st))}, argv=["--json"])
    assert json.loads(capsys.readouterr().out) == {"count": 3}


def test_json_falls_back_to_raw_source_data_without_a_summary(artifact, capsys):
    # An artifact with no summary() should still answer --json usefully.
    app.run(render=render, argv=["--json"])
    assert json.loads(capsys.readouterr().out)["data"]["rows"][0] == {"n": 1}


def test_no_tty_and_no_mode_defaults_to_one_frame(artifact, capsys, monkeypatch):
    # Piping an artifact must print a frame, not spin forever writing
    # escape codes into a pipe nobody reads.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    app.run(render=render, argv=[])
    assert "BODY-MARKER" in capsys.readouterr().out


def test_render_sees_the_manifest_for_policy(artifact, capsys):
    seen = {}

    def check(state, console):
        seen["title"] = state["manifest"].title
        return Text("x")

    app.run(render=check, argv=["--once"])
    assert seen["title"] == "From manifest"


def test_missing_data_file_renders_the_frame_but_exits_nonzero(tmp_path, monkeypatch, capsys):
    """Two halves of one contract. The frame still renders — an artifact's
    empty state is real UI, not a crash. But the exit code says unhealthy,
    with the why on stderr: "no data" printing with exit 0 was
    indistinguishable (to cron, CI, an agent) from a genuinely empty
    artifact."""
    repo = tmp_path / "repo"
    (repo / ".tart").mkdir(parents=True)
    manifest = repo / ".tart" / "thing.json"
    manifest.write_text(json.dumps({"title": "T", "run": "true", "data": "data/absent.json"}))
    monkeypatch.setenv("TART_MANIFEST", str(manifest))
    monkeypatch.chdir(tmp_path)

    def render_empty(state, console):
        return Text("no data" if state.get("data") is None else "data")

    with pytest.raises(SystemExit) as bad:
        app.run(render=render_empty, argv=["--once"])
    captured = capsys.readouterr()
    assert "no data" in captured.out
    assert "does not exist" in captured.err
    assert bad.value.code == 1


def test_artifact_without_a_manifest_still_renders(tmp_path, monkeypatch, capsys):
    # Not every artifact has data or provenance — a clock has neither.
    monkeypatch.delenv("TART_MANIFEST", raising=False)
    monkeypatch.chdir(tmp_path)
    app.run(render=lambda st, c: Text("STANDALONE"), argv=["--once"])
    assert "STANDALONE" in capsys.readouterr().out


def test_state_refuses_to_replace_a_live_widget(tmp_path, capsys):
    """JSON can't describe a Cursor. Replacing one with a dict didn't fail
    loudly — it raised from deep inside a widget, or silently killed the
    keyboard for that frame."""

    from tartifacts import widgets

    with pytest.raises(SystemExit) as exit_code:
        app.run(render=lambda st, c: "x", state={"cursor": widgets.Cursor()},
                argv=["--once", "--state", '{"cursor": {"index": 25}}'])
    assert exit_code.value.code == 1
    assert "cannot replace live values" in capsys.readouterr().err


@pytest.mark.parametrize("held", [
    lambda w: {w()},                        # a set of widgets
    lambda w: {w(): "left"},                # a widget as a dict KEY
    lambda w: [{"deep": {"deeper": w()}}],  # buried three containers down
])
def test_state_refuses_a_live_value_wherever_it_hides(held, capsys):
    """The old enumeration missed sets, dict keys, and deep nesting — each
    a container `--state` could then silently wipe. The rule is now "not
    JSON-native means live", complete by construction."""

    from tartifacts import widgets

    with pytest.raises(SystemExit):
        app.run(render=lambda st, c: "x", state={"box": held(widgets.Cursor)},
                argv=["--once", "--state", '{"box": 0}'])
    assert "cannot replace live values" in capsys.readouterr().err


def test_is_live_classifies_json_vs_objects():
    from pathlib import Path

    from tartifacts import app, widgets, manifest

    # live: not something JSON could have produced
    assert app._is_live(widgets.Cursor()) is True
    assert app._is_live({"a": widgets.Cursor()}) is True         # widget as value
    assert app._is_live({widgets.Cursor(): "x"}) is True         # widget as key
    assert app._is_live({1, widgets.Cursor()}) is True           # widget in a set
    assert app._is_live(manifest.Manifest(path=Path("x"), title="T")) is True

    # data: scalars and containers of scalars, at any depth
    assert app._is_live({"a": 1, "b": [2, 3]}) is False
    assert app._is_live([1, "x", None, True]) is False
    assert app._is_live({1, 2, 3}) is False                      # a set of scalars
    assert app._is_live((1, 2)) is False                         # a tuple of scalars


def test_state_still_merges_plain_data(capsys):
    import re

    app.run(render=lambda st, c: f"detail={st.get('show_detail')}",
            argv=["--once", "--state", '{"show_detail": true}'])
    plain = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)
    assert "detail=True" in plain


def test_json_exercises_render_not_just_summary(artifact, capsys):
    """`--json` is documented as executing the artifact. It used to run
    summary() only, so a render() that crashes on every frame reported a
    clean exit and an agent shipped a dashboard that was a traceback."""

    def crashing_render(state, console):
        raise KeyError("every frame is a crash")

    with pytest.raises((KeyError, SystemExit)):
        app.run(render=crashing_render, summary=lambda st: {"ok": 1}, argv=["--json"])


def test_json_still_prints_the_summary_when_render_is_fine(artifact, capsys):
    app.run(render=render, summary=lambda st: {"count": len(rows(st))}, argv=["--json"])
    assert json.loads(capsys.readouterr().out) == {"count": 3}


def test_state_wins_over_the_source_that_would_overwrite_it(artifact, capsys):
    """`--state '{"data": ...}'` is how you render an artifact against sample
    data — for a screenshot, or to check a shape you don't have locally. The
    source loader ran afterwards and silently put the real file back, so the
    flag did nothing at all for the one key anyone wants to override."""
    app.run(render=render, argv=["--once", "--state", '{"data": {"rows": [1, 2]}}'])
    assert "2 rows" in capsys.readouterr().out          # not the 3 on disk


def test_a_source_not_pinned_by_state_still_loads(artifact, capsys):
    app.run(render=render, argv=["--once", "--state", '{"unrelated": 1}'])
    assert "3 rows" in capsys.readouterr().out          # real data, untouched


def test_stale_data_renders_with_a_warning_but_exit_zero(tmp_path, monkeypatch, capsys):
    """Stale is a warning, not a failure: the numbers are usable, so exit
    stays 0 — but an agent reading `render --json` must be TOLD the data
    is past its declared freshness, or it reads 6-hour-old numbers all
    session (this happened)."""
    import os, time
    repo = tmp_path / "repo"
    (repo / ".tart").mkdir(parents=True)
    (repo / "data").mkdir()
    data = repo / "data" / "d.json"
    data.write_text('{"n": 1}')
    old = time.time() - 7200
    os.utime(data, (old, old))
    manifest = repo / ".tart" / "thing.json"
    manifest.write_text(json.dumps(
        {"title": "T", "run": "true", "data": "data/d.json", "stale_after": "1h"}))
    monkeypatch.setenv("TART_MANIFEST", str(manifest))
    monkeypatch.chdir(tmp_path)

    app.run(render=lambda st, c: Text("fine"), argv=["--json"])
    captured = capsys.readouterr()
    assert "warning: data is 2h old" in captured.err
    assert "stale_after 1h" in captured.err
    assert "tart fetch thing" in captured.err
    assert json.loads(captured.out) == {"data": {"n": 1}}   # numbers still flow


def test_fresh_data_renders_with_no_warning(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    (repo / ".tart").mkdir(parents=True)
    (repo / "data").mkdir()
    (repo / "data" / "d.json").write_text('{"n": 1}')
    manifest = repo / ".tart" / "thing.json"
    manifest.write_text(json.dumps(
        {"title": "T", "run": "true", "data": "data/d.json", "stale_after": "1h"}))
    monkeypatch.setenv("TART_MANIFEST", str(manifest))
    monkeypatch.chdir(tmp_path)

    app.run(render=lambda st, c: Text("fine"), argv=["--json"])
    assert "warning" not in capsys.readouterr().err
