"""An index of manifests tart has seen, so finding one doesn't depend on
guessing where to look.

Scanning alone has a hard limit: it only walks `<root>/` and
`<root>/*/`, because going deeper means thousands of directories. An
artifact inside a git worktree — `frontend-worktrees/some-branch/.tart/`
— is three levels down and simply invisible.

An index alone has the opposite problem: an artifact you've *written* but
never run isn't in it, so `tart list` wouldn't show a manifest sitting
right there on disk.

So: both. The scan finds anything new and shallow; the index remembers
anything tart has ever resolved, however deep. Neither is authoritative —
the manifest files are, which is why every index entry is re-validated on
read (does it still exist, does it still parse) and pruned when it isn't.
That's the same discipline `registry.live()` applies to pids: record
cheaply, verify on use, never trust the record over reality.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import jsonfile, paths, roots


def index_path() -> Path:
    return paths.home() / "index.json"

# tart's own files live in ~/.tart alongside global manifests, so a scan
# there must not mistake them for artifacts. One rule, since listing names
# individually is what let index.json slip through as an "(untitled)" one.
INTERNAL_NAMES = {"config.json", "index.json", "trusted.json"}


def is_internal(path: Path) -> bool:
    # `.state.json` is a legacy sidecar tart no longer writes; the guard
    # stays so leftover files aren't scanned as broken manifests.
    return path.name in INTERNAL_NAMES or path.name.endswith(".state.json")


def _load_raw() -> dict:
    try:
        raw = json.loads(index_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return raw.get("artifacts", {}) if isinstance(raw, dict) else {}


def _save_raw(artifacts: dict) -> None:
    try:
        jsonfile.write(index_path(), {"artifacts": artifacts})
    except OSError:
        pass  # the index is a cache; never fail a command over it


def remember(path: Path) -> None:
    """Record a manifest tart has resolved. Cheap and idempotent — called
    whenever an artifact is looked up or launched, so anything you've used
    once stays findable no matter how deep it lives."""
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        return
    artifacts = _load_raw()
    if artifacts.get(resolved, {}).get("seen"):
        artifacts[resolved]["seen"] = time.time()
    else:
        artifacts[resolved] = {"seen": time.time()}
    _save_raw(artifacts)


def known() -> list[Path]:
    """Indexed manifests that still exist, pruning those that don't. A repo
    that moved or was deleted drops out here rather than lingering as a
    phantom the way a trusted registry would."""
    artifacts = _load_raw()
    alive, changed = [], False
    for raw_path in list(artifacts):
        path = Path(raw_path)
        if path.is_file():
            alive.append(path)
        else:
            del artifacts[raw_path]
            changed = True
    if changed:
        _save_raw(artifacts)
    return alive


def reindex() -> list[Path]:
    """Deep-search every root and remember what's there. This is how a
    manifest that *moved* becomes findable again — pruning drops the old
    path, and only a real search can discover the new one."""
    found = []
    for directory in roots.deep_scan():
        for path in sorted(directory.glob("*.json")):
            if is_internal(path):
                continue
            remember(path)
            found.append(path)
    return found

