import json
import os
import time

import pytest

from tartifacts import manifest


def write_pointer(tmp_path, **fields):
    repo = tmp_path / "repo"
    (repo / ".tart").mkdir(parents=True)
    path = repo / ".tart" / "thing.json"
    path.write_text(json.dumps({"title": "Thing", **fields}))
    return path


def test_parse_duration_units():
    assert manifest.parse_duration("4h") == 4 * 3600
    assert manifest.parse_duration("30m") == 1800
    assert manifest.parse_duration("2d") == 172800
    assert manifest.parse_duration("45") == 45  # bare number is seconds
    assert manifest.parse_duration(90) == 90


def test_parse_duration_rejects_garbage():
    # None, not 0 -- a bad value must read as "no policy declared" rather
    # than "everything is instantly stale".
    assert manifest.parse_duration("soon") is None
    assert manifest.parse_duration(None) is None


def test_root_is_repo_not_dotartifact(tmp_path):
    path = write_pointer(tmp_path, run="x")
    assert manifest.load(path).root == (tmp_path / "repo").resolve()


def test_data_path_resolves_against_repo_root(tmp_path):
    path = write_pointer(tmp_path, data="bin/out.json")
    assert manifest.load(path).data_path == (tmp_path / "repo" / "bin/out.json")


def test_missing_data_file_reports_no_age(tmp_path):
    ptr = manifest.load(write_pointer(tmp_path, data="nope.json", stale_after="1h"))
    assert ptr.data_age() is None
    assert ptr.is_stale() is None  # unjudgeable, distinct from "fresh"


def test_staleness_compares_mtime_to_declared_limit(tmp_path):
    path = write_pointer(tmp_path, data="out.json", stale_after="1h")
    data_file = tmp_path / "repo" / "out.json"
    data_file.write_text("{}")

    ptr = manifest.load(path)
    assert ptr.is_stale() is False

    old = time.time() - 7200  # 2h > the declared 1h
    os.utime(data_file, (old, old))
    assert manifest.load(path).is_stale() is True


def test_no_stale_after_means_unjudged(tmp_path):
    path = write_pointer(tmp_path, data="out.json")
    (tmp_path / "repo" / "out.json").write_text("{}")
    assert manifest.load(path).is_stale() is None


def test_load_bad_json_returns_none(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert manifest.load(bad) is None


@pytest.mark.parametrize("declared", ["0", "0s", "0m", -5, 0, "-3s", "", "soon", "4x"])
def test_a_non_policy_duration_reads_as_no_policy_not_zero(declared):
    """Zero/negative/garbage must be None ("unjudged"), never 0 — which
    would mark every artifact permanently stale the moment it was written."""
    assert manifest.parse_duration(declared) is None


@pytest.mark.parametrize("declared,seconds", [
    ("30s", 30), ("45m", 2700), ("4h", 14400), ("2d", 172800),
    ("1.5h", 5400), (90, 90), (2.5, 2.5), ("  10m  ", 600), ("7", 7),
])
def test_durations_that_are_policies_parse_to_seconds(declared, seconds):
    assert manifest.parse_duration(declared) == seconds


@pytest.mark.parametrize("age,stale", [(99.0, False), (100.0, False), (100.5, True)])
def test_staleness_is_judged_at_the_exact_boundary(tmp_path, monkeypatch, age, stale):
    """`age > stale_after`, so data exactly AT its limit is still fresh.
    A real clock can never land on the boundary, so the comparison is pinned
    with a fixed one — otherwise `>` and `>=` are indistinguishable."""
    data = tmp_path / "d.json"
    data.write_text("{}")
    spec = tmp_path / "m.json"
    spec.write_text(json.dumps({"title": "T", "run": "true",
                                "data": str(data), "stale_after": "100s"}))
    ptr = manifest.load(spec)

    written = os.path.getmtime(data)
    monkeypatch.setattr(manifest.time, "time", lambda: written + age)
    assert ptr.is_stale() is stale
