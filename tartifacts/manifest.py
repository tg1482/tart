"""The `.tart/<name>.json` manifest — what an artifact declares about itself.

Beyond `title`/`run` (presentation), a manifest declares *provenance*: what
file holds the data, what command produces it, and how old that data can
get before it's misleading. That's what makes an artifact a self-contained
artifact rather than a renderer that silently depends on an invisible
crontab line on one particular machine — with these declared, `tart
run` refreshes stale data and `tart list` flags it.

    {
      "title": "Bedrock spend",
      "run":   "$TART_PYTHON bin/dash.py",   // tart's interpreter: has rich+tartifacts
      "data":  "bin/data/bedrock_usage.json",   // relative to the repo root
      "fetch": "$TART_PYTHON bin/snapshot.py", // produces `data`
      "stale_after": "4h",                      // when `data` stops being trustworthy
      "auto_refresh": true                      // re-run `fetch` while the artifact is open
    }

Only `title` and `run` are required; an artifact with no external data source
(everything computed in-process) legitimately has none of the rest.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import jsonfile

DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str | int | float | None) -> float | None:
    """'4h' -> 14400.0. Bare numbers are seconds. None/unparseable -> None,
    which callers treat as "no staleness policy declared" rather than 0."""
    if text is None or isinstance(text, bool):
        return None      # bool is an int in Python: `true` meant 1 second
    if isinstance(text, (int, float)):
        return float(text) if text > 0 else None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd])?\s*", str(text))
    if not match:
        return None
    seconds = float(match.group(1)) * DURATION_UNITS.get(match.group(2) or "s", 1)
    return seconds or None       # "0s" is not a staleness policy


@dataclass
class Manifest:
    path: Path
    title: str
    run: str | None = None
    data: str | None = None
    fetch: str | None = None
    stale_after: float | None = None
    # KEY=VALUE file loaded into `run`/`fetch`'s environment (see envfile).
    # Declared here because the manifest is what trust hashes: pointing it
    # at a different file re-asks, while the file's CONTENTS are data and
    # deliberately outside the hash — like direnv and what .envrc sources.
    env_file: str | None = None
    # `--state` payloads worth checking: the views a keypress reaches.
    # `tart render <name> --states` renders each one headless — a key
    # nobody can press from a pipe is otherwise a view nobody can check,
    # and the crash always lives in the one view the smoke test skipped.
    states: list = field(default_factory=list)
    # Opt-in, because `fetch` can be expensive (a real API call). Off, a
    # artifact only shows staleness and tells you to refresh; on, it keeps
    # its own data fresh and needs no external cron at all.
    auto_refresh: bool = False

    @property
    def root(self) -> Path:
        """The directory `run`/`fetch` should execute from, and that `data`
        is relative to — one level above `.tart/`. Falls back to the
        manifest's own directory for a `~/.tart/` global one, whose
        commands are expected to use absolute paths."""
        resolved = self.path.resolve()
        if resolved.parent.name == ".tart" and resolved.parent.parent != Path.home():
            return resolved.parent.parent
        return resolved.parent

    @property
    def data_path(self) -> Path | None:
        if not self.data:
            return None
        candidate = Path(self.data)
        return candidate if candidate.is_absolute() else self.root / candidate

    @property
    def env_file_path(self) -> Path | None:
        """Like `data_path`, but `~` expands — secrets typically live in
        the home directory precisely so they stay out of the repo."""
        if not self.env_file:
            return None
        candidate = Path(self.env_file).expanduser()
        return candidate if candidate.is_absolute() else self.root / candidate

    def data_age(self) -> float | None:
        """Seconds since `data` was last written, or None if undeclared or
        missing (None for both; the caller distinguishes if it needs to)."""
        path = self.data_path
        if path is None:
            return None
        try:
            return time.time() - os.path.getmtime(path)
        except OSError:
            return None

    def is_stale(self) -> bool | None:
        """True/False, or None when it can't be judged (no `stale_after`
        declared, or no data file to judge)."""
        age = self.data_age()
        if age is None or self.stale_after is None:
            return None
        return age > self.stale_after


def named_scripts(command: str) -> list[str]:
    """Local script files a command refers to — a .py/.sh token that isn't
    an option. The trust refusal shows them (the code is in the script, not
    the manifest), and the live loop watches them to restart on edit."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []  # unbalanced quotes — not our job to parse shell
    return [t for t in tokens if t.endswith((".py", ".sh")) and not t.startswith("-")]


def current() -> Manifest | None:
    """The manifest this process belongs to. `tart run` and `artifact
    fetch` both set TART_MANIFEST, so a script launched either way finds
    its own manifest without hardcoding a path to it."""
    env = os.environ.get("TART_MANIFEST")
    return load(Path(env)) if env else None


def data_path() -> Path:
    """Where this artifact's data lives, per its manifest — for fetch scripts
    to write to and artifacts to read, so the path is declared once instead
    of copied into the writer, the reader, and the manifest.

    Requires the manifest, i.e. run via `tart fetch <name>` (or with
    TART_MANIFEST set) rather than invoking the script bare. Creates the
    parent directory, so a fetch script never needs its own mkdir.
    """
    ptr = current()
    if ptr is None:
        raise RuntimeError(
            "no manifest in scope — run this via `tart fetch <name>`, "
            "or set TART_MANIFEST=/path/to/.tart/<name>.json"
        )
    if ptr.data_path is None:
        raise RuntimeError(f'{ptr.path} declares no "data" path to write to')
    ptr.data_path.parent.mkdir(parents=True, exist_ok=True)
    return ptr.data_path


def write_data(value: Any) -> Path:
    """Write this artifact's data where the manifest says, atomically.

    A live artifact polls its data file by mtime, so a plain
    `open(path, "w")` is visible half-written: the reader gets a JSON error
    and shows "no data yet" until the next fetch. Prefer this over writing
    the path yourself — it is the same temp-file-and-rename tart uses for
    its own state.
    """
    path = data_path()
    jsonfile.write(path, value)
    return path


# What each field must be if present. Checked once, here, so nothing
# downstream has to defend itself: a `"run": ["python", "x.py"]` used to
# reach shlex.split and traceback `tart list` for EVERY artifact in the
# workspace -- including from a manifest you had refused to trust. The
# trust gate treats a manifest as untrusted input; its shape deserves the
# same posture.
FIELD_TYPES = {
    "title": str,
    "run": str,
    "data": str,
    "fetch": str,
    "env_file": str,
    "auto_refresh": bool,
    "stale_after": (str, int, float),
    "states": list,
}


def _shape_problem(raw: dict) -> str | None:
    """The first wrongly-typed field, named, or None if the shape is fine."""
    for key, expected in FIELD_TYPES.items():
        value = raw.get(key)
        if value is not None and not isinstance(value, expected):
            types = expected if isinstance(expected, tuple) else (expected,)
            want = "/".join(t.__name__ for t in types)
            return f'"{key}" must be {want}, not {type(value).__name__}'
    return None


def problem(path: Path) -> str | None:
    """Why this manifest file is unusable, in words — or None if it loads.

    Distinguishes "not valid JSON" from "valid JSON, wrong shape" so a type
    error stops being reported as a parse error with a fix ("repair the
    JSON") that repairs nothing. `load()` collapses all of these to None;
    this is what tells the user which one it was."""
    try:
        raw = json.loads(Path(path).read_text())
    except OSError as bad:
        return f"cannot read the file: {bad}"
    except json.JSONDecodeError as bad:
        return f"not valid JSON: {bad}"
    if not isinstance(raw, dict):
        return f"top-level JSON must be an object, not {type(raw).__name__}"
    return _shape_problem(raw)


def load(path: Path) -> Manifest | None:
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or _shape_problem(raw) is not None:
        return None
    return Manifest(
        path=Path(path),
        title=raw.get("title") or "(untitled)",
        run=raw.get("run"),
        data=raw.get("data"),
        fetch=raw.get("fetch"),
        env_file=raw.get("env_file"),
        stale_after=parse_duration(raw.get("stale_after")),
        states=raw.get("states") or [],
        auto_refresh=bool(raw.get("auto_refresh", False)),
    )
