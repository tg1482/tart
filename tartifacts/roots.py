"""Where to look for artifacts beyond the current directory.

An artifact declared in one repo should be runnable from anywhere without
hand-symlinking it into `~/.tart/` — forgetting that step produces a
confusing "not found" for an artifact that plainly exists.

Rather than a registry mapping names to paths (which drifts the moment a
repo moves or is deleted), this stores only *roots* to scan. The manifest
files stay the single source of truth; roots just widen where we look.

    ~/.tart/config.json   {"roots": ["~/dev/quarkle"]}

Scanning is deliberately shallow — `<root>/.tart/` and
`<root>/*/.tart/` — which covers a workspace of sibling repos without
walking a whole tree.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import jsonfile, paths

def global_dir() -> Path:
    return paths.home()


def config_path() -> Path:
    return paths.home() / "config.json"


def load() -> list[Path]:
    try:
        raw = json.loads(config_path().read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return [Path(os.path.expanduser(r)) for r in raw.get("roots", []) if isinstance(r, str)]


def save(entries: list[Path]) -> None:
    jsonfile.write(config_path(), {"roots": sorted({str(p) for p in entries})})


def add(path: str) -> Path:
    resolved = Path(os.path.expanduser(path)).resolve()
    save(load() + [resolved])
    return resolved


def remove(path: str) -> None:
    target = Path(os.path.expanduser(path)).resolve()
    save([p for p in load() if p.resolve() != target])


# Directories never worth descending into when searching deeply. Without
# this a workspace scan spends its time in dependency trees.
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "target", "dist",
             "build", "__pycache__", ".next", ".cache", "site-packages"}


def deep_scan() -> list[Path]:
    """Every `.tart/` under a configured root, at any depth.

    ~0.9s on a real workspace versus ~30ms for `artifact_dirs()`, which is
    why this is never on the fast path — it runs on an explicit `tart
    reindex`, or once when a lookup would otherwise fail.
    """
    found: list[Path] = []
    for root in load():
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                entries = list(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                if not entry.is_dir() or entry.is_symlink():
                    continue
                if entry.name == ".tart":
                    found.append(entry)
                elif entry.name not in SKIP_DIRS:
                    stack.append(entry)
    return found


def artifact_dirs() -> list[Path]:
    """Every `.tart/` directory worth scanning, in precedence order:
    the current directory first (most specific), then `~/.tart`, then
    each configured root and its immediate children."""
    dirs = [Path(".tart"), global_dir()]
    for root in load():
        dirs.append(root / ".tart")
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        dirs.extend(child / ".tart" for child in children if child.is_dir())
    return [d for d in dirs if d.is_dir()]
