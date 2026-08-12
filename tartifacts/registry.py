"""Which artifacts are running right now — by process, not by multiplexer.

An artifact records itself in `<TART_HOME>/live/` on startup and removes the
entry on exit. Liveness is then *checkable* rather than asserted: every
entry carries a pid, and a reader confirms the process still exists
instead of trusting that cleanup ran.

This replaced tagging herdr/tmux panes, which had two problems. It only
worked inside those two programs — a plain terminal, ssh, screen, zellij
or a systemd unit got no discovery at all. And a tag only cleared on
*clean* exit, so `kill -9` left one behind: `tart list` would report an
artifact live in a pane where nothing was running.

The pane id is still recorded when there is one, because "wA:pG" is more
actionable than a pid — but it's enrichment now, not the mechanism.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from . import jsonfile, paths


def live_dir() -> Path:
    return paths.home() / "live"


@dataclass
class Entry:
    pid: int
    manifest: str
    title: str
    started_at: float
    pane: str | None = None   # multiplexer pane, when running under one
    tty: str | None = None    # falls back to this to say *where* it is

    @property
    def where(self) -> str:
        return self.pane or self.tty or f"pid {self.pid}"


def current_pane() -> str | None:
    """The multiplexer pane running us, when there is one — enrichment for
    `where`, never the mechanism. Innermost wins: tmux inside a herdr pane
    means tmux is what is actually running the process.

    Reads env vars, so no herdr/tmux binary need be on PATH.
    """
    return os.environ.get("TMUX_PANE") or os.environ.get("HERDR_PANE_ID")


def _path(pid: int) -> Path:
    return live_dir() / f"{pid}.json"


def _alive(pid: int) -> bool:
    """Signal 0 checks existence without delivering anything."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def register(manifest: str, title: str, pane: str | None = None) -> None:
    try:
        tty = os.ttyname(0)
    except OSError:
        tty = None
    entry = {
        "pid": os.getpid(),
        "manifest": str(Path(manifest).resolve()),
        "title": title,
        "started_at": time.time(),
        "pane": pane,
        "tty": tty,
    }
    try:
        jsonfile.write(_path(os.getpid()), entry)
    except OSError:
        pass  # discovery is a convenience; never take the artifact down for it


def unregister() -> None:
    try:
        _path(os.getpid()).unlink(missing_ok=True)
    except OSError:
        pass


def live() -> list[Entry]:
    """Every artifact whose process is actually running. Entries for dead
    pids are removed as they're found, so a `kill -9` self-heals on the
    next read rather than lying indefinitely."""
    entries = []
    try:
        files = sorted(live_dir().glob("*.json"))
    except OSError:
        return []
    for path in files:
        try:
            raw = json.loads(path.read_text())
            entry = Entry(**raw)
        except (OSError, json.JSONDecodeError, TypeError):
            path.unlink(missing_ok=True)
            continue
        if _alive(entry.pid):
            entries.append(entry)
        else:
            path.unlink(missing_ok=True)
    return entries
