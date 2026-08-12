"""Resolution across roots — the machinery that replaced hand-symlinking
artifacts into ~/.tart (which was easy to forget, and produced a
confusing 'not found' for an artifact that plainly existed)."""

import json
import os
import time

import pytest

from tartifacts import discover, roots


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A fake workspace of sibling repos, with cwd outside all of them so
    only root-scanning can find anything."""
    root = tmp_path / "work"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(roots, "load", lambda: [root])
    monkeypatch.chdir(elsewhere)
    return root


def declare(repo_dir, name, **fields):
    artifact_dir = repo_dir / ".tart"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"{name}.json"
    path.write_text(json.dumps({"title": fields.pop("title", name), "run": "true", **fields}))
    return path


def test_finds_artifact_in_a_sibling_repo_from_outside(workspace):
    declare(workspace / "repo-a", "alpha")
    assert [p.path.stem for p in discover.declared()] == ["alpha"]
    assert discover.resolve("alpha") is not None


def test_resolves_by_repo_qualified_name(workspace):
    declare(workspace / "repo-a", "alpha")
    ptr = discover.resolve("repo-a/alpha")
    assert ptr is not None and ptr.path.stem == "alpha"


def test_duplicate_names_across_repos_are_reported_not_guessed(workspace):
    declare(workspace / "repo-a", "dup")
    declare(workspace / "repo-b", "dup")
    matches = discover.ambiguous("dup")
    assert len(matches) == 2
    assert sorted(discover.qualified(m) for m in matches) == ["repo-a/dup", "repo-b/dup"]


def test_qualified_name_disambiguates_a_duplicate(workspace):
    declare(workspace / "repo-a", "dup", title="A")
    declare(workspace / "repo-b", "dup", title="B")
    assert discover.resolve("repo-b/dup").title == "B"


def test_same_pointer_reached_twice_is_listed_once(workspace, monkeypatch):
    # A root whose own .tart/ is also a child .tart/ shouldn't double up;
    # dedupe is by resolved path, which also covers ~/.tart symlinks.
    declare(workspace / "repo-a", "alpha")
    monkeypatch.setattr(roots, "load", lambda: [workspace, workspace])
    assert len(discover.declared()) == 1


def test_unknown_name_resolves_to_none(workspace):
    declare(workspace / "repo-a", "alpha")
    assert discover.resolve("nope") is None


def test_state_and_config_files_are_not_artifacts(workspace):
    declare(workspace / "repo-a", "alpha")
    (workspace / "repo-a" / ".tart" / "alpha.json.state.json").write_text("{}")
    (workspace / "repo-a" / ".tart" / "config.json").write_text('{"roots": []}')
    assert [p.path.stem for p in discover.declared()] == ["alpha"]


def test_listing_returns_rows_not_output(tmp_path, monkeypatch):
    """The content is data, so it can be asserted on directly — the printer
    only decides widths."""
    root = tmp_path / "ws"
    (root / "repo" / ".tart").mkdir(parents=True)
    (root / "repo" / ".tart" / "one.json").write_text(
        json.dumps({"title": "One", "run": "true"}))
    monkeypatch.setattr(roots, "load", lambda: [root])
    monkeypatch.chdir(tmp_path)

    rows = discover.listing()
    assert [(r[0], r[1]) for r in rows] == [("one", "One")]


def test_printing_a_listing_never_exceeds_the_terminal(tmp_path, monkeypatch, capsys):
    """A fixed 42-wide path column wrapped every row on a narrow terminal."""
    root = tmp_path / "ws"
    deep = root / "a-really-quite-long-repository-name-here" / ".tart"
    deep.mkdir(parents=True)
    (deep / "one.json").write_text(json.dumps({"title": "One", "run": "true"}))
    monkeypatch.setattr(roots, "load", lambda: [root])
    monkeypatch.chdir(tmp_path)

    for width in (120, 80, 60, 40):
        discover.print_listing(width=width)
        for line in capsys.readouterr().out.splitlines():
            assert len(line) <= width, f"{width} cols: {len(line)}-char line"


def test_a_manifest_that_does_not_parse_is_reported_not_hidden(tmp_path, monkeypatch, capsys):
    """It vanishes from declared(), so `tart list` used to say "No artifacts
    declared" about a directory plainly containing one — exit 0, pointing
    you at `roots add`, which isn't the problem."""
    root = tmp_path / "ws"
    (root / "repo" / ".tart").mkdir(parents=True)
    (root / "repo" / ".tart" / "broken.json").write_text("not json{")
    monkeypatch.setattr(roots, "load", lambda: [root])
    monkeypatch.chdir(tmp_path)

    assert [p.name for p, _ in discover.unreadable()] == ["broken.json"]
    assert discover.print_listing(width=100) == 1        # non-zero for the CLI
    assert "not valid JSON" in capsys.readouterr().out


def test_a_wrongly_typed_field_is_named_not_called_a_parse_error(tmp_path, monkeypatch, capsys):
    """`{"run": ["python","x.py"]}` is valid JSON; saying "does not parse,
    fix: repair the JSON" sent the user hunting a syntax error that isn't
    there. The field and its expected type are named instead."""
    root = tmp_path / "ws"
    (root / "repo" / ".tart").mkdir(parents=True)
    (root / "repo" / ".tart" / "typed.json").write_text('{"title":"T","run":["python","x.py"]}')
    monkeypatch.setattr(roots, "load", lambda: [root])
    monkeypatch.chdir(tmp_path)

    (path, reason), = discover.unreadable()
    assert path.name == "typed.json"
    assert '"run" must be str' in reason and "list" in reason


def test_elide_keeps_both_ends_of_a_path():
    # Dropping only the front left the useless middle of a long temp path
    # and lost the workspace name that identifies it.
    long = "/private/tmp/claude-501/-Users-someone-dev/abc-123-def/scratchpad/sim/b2_debug"
    short = discover.elide(long, 40)
    assert len(short) <= 40
    assert short.startswith("/private")      # where it is
    assert short.endswith("b2_debug")        # and which one it is


def test_the_listing_marks_stale_data_and_leaves_fresh_data_unmarked(tmp_path, monkeypatch):
    """`tart list` is now the only thing that surfaces staleness. Inverting
    the check showed "⚠ data stale" on fresh artifacts and hid it on stale
    ones — and nothing asserted the marker."""
    root = tmp_path / "ws"
    for name, age in (("fresh", 0), ("stale", 7200)):
        repo = root / name
        (repo / ".tart").mkdir(parents=True)
        data = repo / "d.json"
        data.write_text("{}")
        os.utime(data, (time.time() - age, time.time() - age))
        (repo / ".tart" / f"{name}.json").write_text(json.dumps({
            "title": name, "run": "true", "data": "d.json", "stale_after": "1h",
        }))
    monkeypatch.setattr(roots, "load", lambda: [root])
    monkeypatch.chdir(tmp_path)

    marked = {name: "stale" in status for name, _, _, status in discover.listing()}
    assert marked == {"fresh": False, "stale": True}
