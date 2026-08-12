"""Writing JSON must never destroy the file that was already there.

`open(path, "w")` truncates first, so an interrupted write leaves a
half-written file. Every reader here treats unparseable as empty — which
self-heals for a cache, but for `config.json` it silently unregisters every
workspace and every artifact stops being found.
"""

import json
import os

import pytest

from tartifacts import index, jsonfile, paths, registry, roots


def test_write_then_read_round_trip(tmp_path):
    path = tmp_path / "a.json"
    jsonfile.write(path, {"x": 1})
    assert json.loads(path.read_text()) == {"x": 1}


def test_write_creates_missing_parents(tmp_path):
    path = tmp_path / "deep" / "deeper" / "a.json"
    jsonfile.write(path, {"x": 1})
    assert path.is_file()


def test_write_refuses_what_json_cannot_represent(tmp_path):
    """Strict by default. `default=str` used to apply everywhere, so a set,
    a Decimal or a stray object became a quoted string and the write
    reported success — `write_data({...})` wrote the literal `"{Ellipsis}"`
    and exited 0, turning a loud bug into a silent one."""
    path = tmp_path / "a.json"
    with pytest.raises(TypeError):
        jsonfile.write(path, {"when": object()})
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []      # and no temp file left behind


def test_a_summary_may_opt_into_stringifying(tmp_path):
    # A summary is best-effort display data: a datetime in one must not take
    # a live artifact down.
    from datetime import datetime

    path = tmp_path / "a.json"
    jsonfile.write(path, {"when": datetime(2026, 1, 1)}, default=str)
    assert "2026-01-01" in path.read_text()


def test_write_data_refuses_a_value_it_cannot_represent(tmp_path, monkeypatch):
    """The doc's own fetch example was `write_data({...})` — a set holding
    Ellipsis. It wrote `"{Ellipsis}"`, printed nothing, and exited 0."""
    from tartifacts import manifest

    repo = tmp_path / "repo"
    (repo / ".tart").mkdir(parents=True)
    spec = repo / ".tart" / "a.json"
    spec.write_text(json.dumps({"title": "A", "run": "true", "data": "data/a.json"}))
    monkeypatch.setenv("TART_MANIFEST", str(spec))

    with pytest.raises(TypeError):
        manifest.write_data({...})
    assert not (repo / "data" / "a.json").exists()


def test_a_failed_write_leaves_the_previous_file_intact(tmp_path, monkeypatch):
    """The whole point. Before this, the old content was already gone by
    the time the write failed."""
    path = tmp_path / "config.json"
    jsonfile.write(path, {"roots": ["/one", "/two"]})

    def die(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", die)
    with pytest.raises(OSError):
        jsonfile.write(path, {"roots": ["/three"]})

    assert json.loads(path.read_text()) == {"roots": ["/one", "/two"]}


def test_a_failed_write_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    jsonfile.write(path, {"a": 1})

    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("nope")))
    with pytest.raises(OSError):
        jsonfile.write(path, {"a": 2})

    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


def test_the_writes_that_matter_all_go_through_it(tmp_path, monkeypatch):
    """Regression guard: each of these used a plain truncating write, and
    the config one is the costly failure — no roots means no artifacts."""
    calls = []
    monkeypatch.setattr(jsonfile, "write", lambda p, v: calls.append(p.name))

    roots.save([tmp_path / "ws"])
    index.remember(tmp_path / "x.json")
    registry.register(str(tmp_path / "x.json"), "X")

    assert "config.json" in calls
    assert "index.json" in calls
    assert f"{os.getpid()}.json" in calls


# --- TART_HOME -------------------------------------------------------------

def test_every_state_path_follows_tart_home(tmp_path, monkeypatch):
    """One seam instead of four constants. A module added later is isolated
    by default rather than writing to the developer's real home until
    someone notices."""
    monkeypatch.setenv(paths.ENV_VAR, str(tmp_path / "elsewhere"))
    assert paths.home() == tmp_path / "elsewhere"
    for path in (index.index_path(), roots.config_path(), registry.live_dir()):
        assert path.is_relative_to(tmp_path / "elsewhere")


def test_tart_home_defaults_to_the_dotfile_dir(monkeypatch):
    # Not XDG: honouring XDG_STATE_HOME would relocate an existing ~/.tart
    # out from under its owner, unregistering every root silently.
    monkeypatch.delenv(paths.ENV_VAR, raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", "/somewhere/else")
    assert paths.home() == paths.DEFAULT
    assert paths.home().name == ".tart"


# --- artifact data --------------------------------------------------------

def test_write_data_is_atomic_so_a_live_artifact_never_reads_a_torn_file(tmp_path, monkeypatch):
    """The gap this closes: tart wrote its OWN files atomically but the
    template every artifact copies used a plain open(). A dashboard polls
    by mtime, so a half-written file reads as "no data yet"."""
    from tartifacts import app, manifest

    repo = tmp_path / "repo"
    (repo / ".tart").mkdir(parents=True)
    spec = repo / ".tart" / "a.json"
    spec.write_text(json.dumps({"title": "A", "run": "true", "data": "data/a.json"}))
    monkeypatch.setenv("TART_MANIFEST", str(spec))

    written = manifest.write_data({"rows": [1, 2, 3]})
    assert app.FileSource(written).read_now() == {"rows": [1, 2, 3]}

    # A failed rewrite leaves the previous payload readable, not a stub.
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(OSError):
        manifest.write_data({"rows": [9]})
    assert app.FileSource(written).read_now() == {"rows": [1, 2, 3]}


def test_write_data_creates_the_declared_directory(tmp_path, monkeypatch):
    from tartifacts import manifest

    repo = tmp_path / "repo"
    (repo / ".tart").mkdir(parents=True)
    spec = repo / ".tart" / "a.json"
    spec.write_text(json.dumps({"title": "A", "run": "true", "data": "deep/er/a.json"}))
    monkeypatch.setenv("TART_MANIFEST", str(spec))

    assert manifest.write_data({"x": 1}).is_file()
