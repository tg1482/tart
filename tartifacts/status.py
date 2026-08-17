"""The flight recorder: what happened the last time an artifact's fetch ran.

A fetch has three triggers — a human at the CLI, `tart run`'s pre-launch
refresh, and the background keeper — and only the first happens where a
person can see it. The other two used to discard their output entirely: a
cron fetch once failed for 15 hours with `uv: command not found` while the
dashboard sat frozen on old numbers, and nothing anywhere said why.

So every fetch, however triggered, records its outcome here:

    <TART_HOME>/artifacts/<name>-<hash>/status.json   the last outcome
    <TART_HOME>/artifacts/<name>-<hash>/fetch.log     appended output, capped

`tart list` reads it to say "✗ fetch failed", the artifact reads it to show
a warning bar, and `tart logs <name>` prints it. The directory is keyed by
the manifest's resolved path (hashed, with the stem kept for humans), so
two repos declaring the same name don't share a record.

Recording never raises: observability must not be able to take down the
thing it observes. A failure to write simply loses the record, the same
posture `registry` takes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from . import jsonfile, paths

# What gets recorded is later PRINTED — by `tart logs`, the warning bar,
# `tart list`. A fetch's stderr can carry terminal escape sequences (its
# own colours, or an API error body someone else controls), and replaying
# those at view time is how a log clears your screen or retitles your
# window. Strip everything but newline and tab at record time; colours
# read fine stripped, and diagnosis text survives.
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# Fetch output can contain secrets (a traceback echoing a header, an
# env-var dump) and now persists — so the recorder's files are private to
# the owner, not left to the umask.
DIR_MODE, FILE_MODE = 0o700, 0o600

# Enough context to diagnose a failing fetch; small enough to store whole.
TAIL_CHARS = 2000
# The log is append-only, so a fetch failing every 30s for a week must not
# grow without bound. When it passes the cap it's cut back to the tail.
LOG_CAP = 256_000
LOG_KEEP = 64_000


def artifact_dir(manifest_path: Path) -> Path:
    resolved = str(Path(manifest_path).resolve())
    digest = hashlib.sha1(resolved.encode()).hexdigest()[:8]
    return paths.home() / "artifacts" / f"{Path(manifest_path).stem}-{digest}"


def record_fetch(
    manifest_path: Path,
    *,
    trigger: str,                  # "cli" | "run" | "keeper"
    duration: float,
    exit_code: int | None = None,  # None when it never got to exit (see error)
    error: str | None = None,      # timeout / spawn failure / postcondition, in words
    output: str = "",              # combined stdout+stderr, tail kept
    path: str | None = None,       # the PATH the fetch ran under
) -> dict:
    """Write the outcome down and return it (so a live keeper can also hold
    it in memory without re-reading the file it just wrote).

    PATH is recorded because it's the variable that actually differs
    between a shell, the keeper, and cron — `uv: command not found` from a
    cron line is a PATH diagnosis, and the record should carry it."""
    entry = {
        "at": time.time(),
        "trigger": trigger,
        "duration": round(duration, 2),
        "exit_code": exit_code,
        "ok": exit_code == 0 and error is None,
        "error": CONTROL_CHARS.sub("", error) if error else error,
        "output_tail": CONTROL_CHARS.sub("", output[-TAIL_CHARS:]),
        "path": path,
    }
    directory = artifact_dir(manifest_path)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, DIR_MODE)
        jsonfile.write(directory / "status.json", {"fetch": entry})
        os.chmod(directory / "status.json", FILE_MODE)
        _append_log(directory / "fetch.log", entry)
    except OSError:
        pass
    return entry


def last_fetch(manifest_path: Path) -> dict | None:
    """The recorded outcome, or None when no fetch has ever been recorded."""
    try:
        raw = json.loads((artifact_dir(manifest_path) / "status.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    entry = raw.get("fetch") if isinstance(raw, dict) else None
    return entry if isinstance(entry, dict) else None


def describe(entry: dict) -> str:
    """'exit 127' / 'timed out after 600s' — the short why, for one-line
    surfaces like `tart list` and the in-app warning bar."""
    if entry.get("error"):
        return str(entry["error"])
    return f"exit {entry.get('exit_code')}"


def log_path(manifest_path: Path) -> Path:
    return artifact_dir(manifest_path) / "fetch.log"


def log_tail(manifest_path: Path, lines: int = 60) -> str:
    try:
        text = log_path(manifest_path).read_text()
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _append_log(path: Path, entry: dict) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry["at"]))
    verdict = "ok" if entry["ok"] else f"FAILED ({describe(entry)})"
    header = f"── {stamp} {entry['trigger']} {verdict} in {entry['duration']}s"
    body = entry["output_tail"].rstrip()  # already stripped of control chars
    with open(path, "a") as f:
        f.write(header + "\n" + (body + "\n" if body else ""))
    os.chmod(path, FILE_MODE)
    _cap(path)


def _cap(path: Path) -> None:
    try:
        if path.stat().st_size <= LOG_CAP:
            return
        text = path.read_text()
    except OSError:
        return
    kept = text[-LOG_KEEP:]
    cut = kept.find("\n── ")  # resume at an entry boundary, not mid-output
    kept = kept[cut + 1:] if cut != -1 else kept
    try:
        path.write_text(kept)  # plain write: worst case a reader sees a torn log
    except OSError:
        pass
