"""Roots — the config that makes an artifact in one repo runnable from
anywhere. Stores *where to look*, never a name→path map, so nothing drifts
when a repo moves or is deleted."""

import json

import pytest

from tartifacts import roots


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def test_no_config_means_no_roots():
    assert roots.load() == []


def test_add_then_load_round_trip(tmp_path):
    target = tmp_path / "ws"
    target.mkdir()
    roots.add(str(target))
    assert roots.load() == [target.resolve()]


def test_adding_the_same_root_twice_keeps_one(tmp_path):
    target = tmp_path / "ws"
    target.mkdir()
    roots.add(str(target))
    roots.add(str(target))
    assert len(roots.load()) == 1


def test_remove(tmp_path):
    target = tmp_path / "ws"
    target.mkdir()
    roots.add(str(target))
    roots.remove(str(target))
    assert roots.load() == []


def test_tilde_is_expanded(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    added = roots.add("~/ws")
    assert "~" not in str(added)


def test_corrupt_config_reads_as_no_roots(tmp_path):
    roots.config_path().parent.mkdir(parents=True, exist_ok=True)
    roots.config_path().write_text("{not json")
    assert roots.load() == []


def test_scan_order_puts_the_current_directory_first(tmp_path):
    """Precedence: a local .tart/ must beat a workspace one, so a repo can
    shadow a global artifact with its own."""
    (tmp_path / ".tart").mkdir()
    ws = tmp_path / "ws"
    (ws / ".tart").mkdir(parents=True)
    roots.add(str(ws))
    dirs = roots.artifact_dirs()
    assert dirs[0].name == ".tart"
    assert dirs[0].resolve() == (tmp_path / ".tart").resolve()


def test_scan_includes_each_root_child(tmp_path):
    ws = tmp_path / "ws"
    for repo in ("a", "b"):
        (ws / repo / ".tart").mkdir(parents=True)
    roots.add(str(ws))
    found = {d.parent.name for d in roots.artifact_dirs()}
    assert {"a", "b"} <= found


def test_a_root_that_no_longer_exists_is_skipped(tmp_path):
    # A deleted workspace must not break discovery for everything else.
    # (~/.tart is always scanned, so the list isn't empty — the point is
    # that the missing root contributes nothing and raises nothing.)
    missing = tmp_path / "gone"
    roots.save([missing])
    assert all(missing not in d.parents and d != missing for d in roots.artifact_dirs())


def test_config_is_json_with_a_roots_key(tmp_path):
    target = tmp_path / "ws"
    target.mkdir()
    roots.add(str(target))
    assert "roots" in json.loads(roots.config_path().read_text())


def test_adding_a_second_root_keeps_the_first(tmp_path, monkeypatch):
    """`save(load() + [new])`. Regressing to `save([new])` silently drops
    every workspace configured earlier — the user adds a second repo and the
    first one's artifacts vanish from `tart list` with no error."""
    monkeypatch.setenv("TART_HOME", str(tmp_path / "home"))
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()

    roots.add(str(first))
    roots.add(str(second))
    assert sorted(p.name for p in roots.load()) == ["one", "two"]


def test_deep_scan_does_not_follow_symlinked_directories(tmp_path, monkeypatch):
    """A symlink loop makes the scan hang or walk out of the workspace across
    the whole disk. `entry.is_symlink()` is the only thing preventing it."""
    monkeypatch.setenv("TART_HOME", str(tmp_path / "home"))
    root = tmp_path / "ws"
    (root / "real").mkdir(parents=True)
    (root / "real" / ".tart").mkdir()
    outside = tmp_path / "outside"
    (outside / ".tart").mkdir(parents=True)
    (root / "link").symlink_to(outside)          # would escape the workspace
    (root / "loop").symlink_to(root)             # would never terminate
    roots.add(str(root))

    found = roots.deep_scan()
    assert [p.parent.name for p in found] == ["real"]
