"""Run the documentation.

Every previous doc bug was found by a person copying an example and getting
a traceback: a missing `Panel` import, undefined `COLUMNS`, and a fetch
example writing `rows` while the render example read `daily`. Checking that
the examples *parse*, or that their names are defined, caught none of those
— the producer and the consumer were each fine alone.

So this assembles a real artifact out of the doc's own blocks and runs it.
"""

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

from tartifacts import cli

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = cli.SKILL_PATH.read_text()


def block_containing(needle: str) -> str:
    blocks = [b for b in re.findall(r"```python\n(.*?)```", SKILL, re.S) if needle in b]
    assert len(blocks) == 1, f"expected exactly one python block with {needle!r}, got {len(blocks)}"
    return blocks[0]


@pytest.fixture
def doc_artifact(tmp_path):
    """A working repo built only from what the doc shows."""
    home = tmp_path / "home"
    (home / ".tart").mkdir(parents=True)
    repo = tmp_path / "repo"
    (repo / ".tart").mkdir(parents=True)

    (repo / "snapshot.py").write_text(block_containing("write_data"))
    (repo / "dash.py").write_text(block_containing("def summary("))
    (repo / ".tart" / "doc.json").write_text(json.dumps({
        "title": "Doc",
        "run": f"{sys.executable} dash.py",
        "data": "data/doc.json",
        "fetch": f"{sys.executable} snapshot.py",
        "stale_after": "1h",
    }))
    env = {**os.environ, "TART_HOME": str(home / ".tart"), "PYTHONPATH": REPO}
    env.pop("TART_MANIFEST", None)

    def tart(*args):
        return subprocess.run([sys.executable, "-m", "tartifacts.cli", *args],
                              capture_output=True, text=True, cwd=str(repo), env=env, timeout=60)

    tart("trust", "doc")
    return tart


def test_the_fetch_example_runs(doc_artifact):
    result = doc_artifact("fetch", "doc")
    assert result.returncode == 0, result.stderr


def test_the_render_example_renders_what_the_fetch_example_wrote(doc_artifact):
    """The one that matters: the two examples have to agree on the data
    shape. They didn't — `KeyError: 'daily'` on a new user's first render."""
    doc_artifact("fetch", "doc")
    result = doc_artifact("render", "doc")
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout.strip()


def test_the_documented_summary_reads_as_json(doc_artifact):
    doc_artifact("fetch", "doc")
    result = doc_artifact("render", "doc", "--json")
    assert result.returncode == 0, result.stderr
    assert isinstance(json.loads(result.stdout), dict)


def test_the_examples_survive_the_missing_data_they_document(doc_artifact):
    """The doc warns that `state["data"]` is None when the file is missing.
    Its own examples have to honour that."""
    result = doc_artifact("render", "doc")          # never fetched
    assert "Traceback" not in result.stderr, result.stderr


# --- README drift ----------------------------------------------------------
# skill.md has drift tests and stayed accurate; the README had none and
# accumulated a dead `tart doctor` reference, a mangled sentence left by its
# removal, a stale artifact count, and no mention of `tart trust` at all —
# which is required before anything runs, so the quick start could not work.

README = (pathlib.Path(__file__).parent.parent / "README.md").read_text()


def test_every_command_the_readme_shows_is_dispatchable():
    shown = set(re.findall(r"^tart ([a-z][a-z-]*)", README, re.M))
    real = set(re.findall(r'cmd == "([a-z]+)"', (pathlib.Path(__file__).parent.parent
                                                 / "tartifacts" / "cli.py").read_text()))
    assert shown <= real, f"README documents commands that don't exist: {shown - real}"


def test_the_readme_mentions_trust_because_nothing_runs_without_it():
    assert "tart trust" in README


def test_the_readme_does_not_advertise_removed_features():
    for gone in ("tart doctor", "tart new", ".state.json", "insert mode"):
        assert gone not in README.lower().replace("`", ""), f"README still mentions {gone}"


def test_the_install_name_matches_the_package():
    pyproject = (pathlib.Path(__file__).parent.parent / "pyproject.toml").read_text()
    name = re.search(r'^name = "([^"]+)"', pyproject, re.M).group(1)
    assert f"uv tool install {name}" in README
