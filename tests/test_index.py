"""The index: remember where a manifest was, so finding it later doesn't
depend on the scan reaching it.

The scan only walks `<root>/` and `<root>/*/`, because depth 3 in a real
workspace is thousands of directories. That makes an artifact inside a git
worktree invisible. The index closes that gap — but it's a cache, not a
source of truth, so every entry is re-validated on read.
"""

import json

from tartifacts import discover, index, manifest, paths, roots


def write_manifest(directory, name="thing", title="Thing"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps({"title": title, "run": "true"}))
    return path


def test_remember_then_known_round_trip(tmp_path):
    path = write_manifest(tmp_path / "repo" / ".tart")
    index.remember(path)
    assert index.known() == [path.resolve()]


def test_remembering_twice_keeps_one_entry(tmp_path):
    path = write_manifest(tmp_path / "repo" / ".tart")
    index.remember(path)
    index.remember(path)
    assert len(index.known()) == 1


def test_a_deleted_manifest_is_pruned_on_read(tmp_path):
    # The failure mode that makes a trusted registry dangerous: the repo
    # moved or was deleted and the index still points at it.
    path = write_manifest(tmp_path / "repo" / ".tart")
    index.remember(path)
    path.unlink()
    assert index.known() == []
    assert json.loads(index.index_path().read_text())["artifacts"] == {}


def test_corrupt_index_reads_as_empty(tmp_path):
    index.index_path().parent.mkdir(parents=True, exist_ok=True)
    index.index_path().write_text("{not json")
    assert index.known() == []


def test_an_unwritable_index_never_raises(monkeypatch, tmp_path):
    # The index is a cache; an unwritable HOME must not take a command
    # down. Blocked for real (a file where a directory needs to be) rather
    # than by stubbing the save, which would bypass the guard under test.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    monkeypatch.setenv(paths.ENV_VAR, str(blocker / "sub"))

    path = write_manifest(tmp_path / "repo" / ".tart")
    index.remember(path)      # must not raise
    assert index.known() == []


# --- the reason it exists ---------------------------------------------------

def test_an_artifact_too_deep_for_the_scan_is_invisible(tmp_path, monkeypatch):
    """Baseline: three levels down, the scan can't see it."""
    root = tmp_path / "ws"
    deep = root / "repo" / "worktrees" / "a-branch" / ".tart"
    write_manifest(deep, name="deep")
    monkeypatch.setattr(roots, "load", lambda: [root])
    monkeypatch.chdir(tmp_path)

    assert [p.path.stem for p in discover.declared()] == []


def test_a_lookup_that_would_fail_deep_searches_first(tmp_path, monkeypatch):
    """A bare name for a too-deep artifact resolves anyway: rather than
    give up, tart deep-searches the roots once and retries."""
    root = tmp_path / "ws"
    deep = root / "repo" / "worktrees" / "a-branch" / ".tart"
    write_manifest(deep, name="deep")
    monkeypatch.setattr(roots, "load", lambda: [root])
    monkeypatch.chdir(tmp_path)

    assert discover.resolve("deep") is not None
    assert "deep" in [p.path.stem for p in discover.declared()]   # now indexed


def test_the_deep_search_only_runs_when_a_lookup_would_fail(tmp_path, monkeypatch):
    """The fast path must stay fast — ~30ms vs ~1s for a deep search."""
    root = tmp_path / "ws"
    write_manifest(root / "repo" / ".tart", name="shallow")
    monkeypatch.setattr(roots, "load", lambda: [root])
    monkeypatch.chdir(tmp_path)

    calls = []
    monkeypatch.setattr(roots, "deep_scan", lambda: calls.append(1) or [])
    assert discover.resolve("shallow") is not None
    assert calls == []                       # found by scan, never searched deep

    assert discover.resolve("absent") is None
    assert calls == [1]                      # only on the failing lookup


def test_reindex_finds_manifests_at_any_depth(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    write_manifest(root / "a" / ".tart", name="one")
    write_manifest(root / "a" / "b" / "c" / ".tart", name="two")
    monkeypatch.setattr(roots, "load", lambda: [root])

    found = index.reindex()
    assert sorted(p.stem for p in found) == ["one", "two"]
    assert len(index.known()) == 2


def test_reindex_skips_dependency_trees(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    write_manifest(root / "repo" / "node_modules" / "pkg" / ".tart", name="vendored")
    write_manifest(root / "repo" / ".tart", name="real")
    monkeypatch.setattr(roots, "load", lambda: [root])

    assert [p.stem for p in index.reindex()] == ["real"]


def test_reindex_recovers_a_manifest_that_moved(tmp_path, monkeypatch):
    """The gap this closes: pruning removes the old path, and only a search
    can discover the new one."""
    root = tmp_path / "ws"
    original = root / "repo" / "worktrees" / "old-branch" / ".tart"
    path = write_manifest(original, name="moved")
    monkeypatch.setattr(roots, "load", lambda: [root])
    monkeypatch.chdir(tmp_path)

    index.remember(path)
    assert len(index.known()) == 1

    moved_to = root / "repo" / "worktrees" / "new-branch" / ".tart"
    moved_to.mkdir(parents=True)
    path.rename(moved_to / "moved.json")

    assert index.known() == []                    # old path pruned
    assert discover.resolve("moved") is not None  # deep search re-finds it
    assert index.known()[0].parent == moved_to


def test_resolving_by_name_also_indexes_it(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    write_manifest(root / "repo" / ".tart", name="shallow")
    monkeypatch.setattr(roots, "load", lambda: [root])
    monkeypatch.chdir(tmp_path)

    assert discover.resolve("shallow") is not None
    assert [p.name for p in index.known()] == ["shallow.json"]


def test_scan_still_wins_for_collisions(tmp_path, monkeypatch):
    """Nearby artifacts must outrank remembered ones, so a repo can shadow
    a global name with its own."""
    root = tmp_path / "ws"
    near = write_manifest(root / "near" / ".tart", name="dup", title="NEAR")
    far = write_manifest(tmp_path / "elsewhere" / ".tart", name="dup", title="FAR")
    index.remember(far)
    monkeypatch.setattr(roots, "load", lambda: [root])
    monkeypatch.chdir(tmp_path)

    assert discover.resolve("dup").title == "NEAR"
    assert near.exists() and far.exists()


def test_index_survives_a_manifest_that_stops_parsing(tmp_path, monkeypatch):
    path = write_manifest(tmp_path / "repo" / ".tart")
    index.remember(path)
    path.write_text("{not json")
    monkeypatch.chdir(tmp_path)
    # Still on disk, so it stays indexed — but it can't load, so it isn't
    # reported as a declared artifact.
    assert index.known() == [path.resolve()]
    assert manifest.load(path) is None
    assert discover.declared() == []
