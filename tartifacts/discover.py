"""Finding artifacts: what's *declared* (manifest files) crossed with what's
*live* (a process actually running one, per `registry`).

Declared artifacts come from every `.tart/` directory `roots.artifact_dirs()`
turns up — the current directory, `~/.tart`, and each configured root's
own and immediate children's. So an artifact declared in one repo is runnable
from anywhere, with no symlinking step to forget.

Names are the manifest's filename. When two repos declare the same name,
both keep working via a `<repo>/<name>` qualifier and the ambiguity is
reported rather than silently resolved to whichever was scanned first.
"""

from __future__ import annotations

import shutil
from collections import Counter
from pathlib import Path

from . import index, manifest as manifest_mod, registry, roots
from .manifest import Manifest


def unreadable() -> list[tuple[Path, str]]:
    """(path, reason) for manifest files that exist but don't load. They
    vanish from `declared()`, so without this `tart list` says "No artifacts
    declared" about a directory plainly containing one. The reason
    distinguishes bad JSON from a valid file with a wrongly-typed field."""
    candidates = [
        path for directory in roots.artifact_dirs()
        for path in sorted(directory.glob("*.json"))
    ]
    candidates += index.known()      # the deep ones; declared() reads these too
    broken, seen = [], set()
    for path in candidates:
        key = _canon(path)
        if key in seen or index.is_internal(path):
            continue
        seen.add(key)
        reason = manifest_mod.problem(path)
        if reason is not None:
            broken.append((path, reason))
    return broken


def declared() -> list[Manifest]:
    """Every visible manifest, nearest-first, deduped by resolved path.

    Two sources, because neither alone is enough: the scan finds anything
    new (but only two levels deep), the index remembers anything tart has
    resolved before (however deep). Scan first so a nearby artifact still
    wins on name collisions.
    """
    candidates: list[Path] = []
    for directory in roots.artifact_dirs():
        for path in sorted(directory.glob("*.json")):
            if index.is_internal(path):
                continue
            candidates.append(path)
    candidates.extend(index.known())

    found: list[Manifest] = []
    seen: set[Path] = set()
    for path in candidates:
        key = _canon(path)
        if key in seen:
            continue
        loaded = manifest_mod.load(path)
        if loaded is not None:
            seen.add(key)
            found.append(loaded)
    return found


def qualified(found: Manifest) -> str:
    """`<repo>/<name>` — how to name an artifact unambiguously."""
    return f"{found.root.name}/{found.path.stem}"


def resolve(name_or_path: str) -> Manifest | None:
    """Accepts a bare name (`bedrock`), a repo-qualified name
    (`wss-service/bedrock`), or a direct path. Bare names prefer the
    nearest match, so a cwd-local artifact beats a global one."""
    path = Path(name_or_path)
    if name_or_path.endswith(".json") and path.is_file():
        found = manifest_mod.load(path)
        if found is not None:
            index.remember(found.path)   # a direct path is how deep ones get known
        return found

    match = _find(name_or_path, declared())
    if match is None:
        # About to fail. A manifest that moved was pruned from the index and
        # sits below the scan's two levels, so only a deep search can find
        # it again — worth ~1s on the path that would otherwise say "no such
        # artifact", never on the fast path.
        index.reindex()
        match = _find(name_or_path, declared())
    if match is not None:
        index.remember(match.path)
    return match


def _find(name: str, manifests: list[Manifest]) -> Manifest | None:
    for candidate in manifests:
        if candidate.path.stem == name:
            return candidate
    for candidate in manifests:
        if qualified(candidate) == name:
            return candidate
    return None


def ambiguous(name: str) -> list[Manifest]:
    """Manifests sharing a bare name — used to tell the user to qualify it
    instead of quietly running whichever was found first."""
    return [p for p in declared() if p.path.stem == name]


def listing() -> list[tuple[str, str, str, str]]:
    """(name, title, where it lives, status) for everything declared, then
    anything live whose manifest isn't declared anywhere.

    Rows, not printing: what to show and how to show it are separate
    concerns, and this way the content is testable without capturing
    stdout."""
    manifests = declared()
    live = registry.live()
    live_by_path = {_canon(Path(e.manifest)): e for e in live}
    duplicated = Counter(m.path.stem for m in manifests)

    rows = []
    for found in manifests:
        entry = live_by_path.get(_canon(found.path))
        # Qualify only when the bare name is ambiguous — otherwise the short
        # name is the one you'd actually type.
        name = qualified(found) if duplicated[found.path.stem] > 1 else found.path.stem
        status = f"[live in {entry.where}]" if entry else ""
        if found.is_stale():
            status = (status + " ⚠ data stale").strip()
        rows.append((name, found.title, str(found.root), status))

    declared_paths = {_canon(m.path) for m in manifests}
    return rows + [
        ("(undeclared)", entry.title, entry.manifest, f"[live in {entry.where}]")
        for entry in live if _canon(Path(entry.manifest)) not in declared_paths
    ]


def print_listing(width: int | None = None) -> int:
    """Returns the number of unreadable manifests, so the CLI can exit
    non-zero rather than reporting nothing at all about them."""
    rows = listing()
    broken = unreadable()
    if not rows and not broken:
        print("No artifacts declared. Add a workspace with: tart roots add <path>")
        return 0
    # Columns sized to the content, path given whatever is left and elided
    # from the front when it doesn't fit. A fixed 42 wrapped every row on a
    # narrow terminal.
    width = width or shutil.get_terminal_size().columns
    name_w, title_w, status_w = (max((len(r[i]) for r in rows), default=0) for i in (0, 1, 3))
    path_w = max(12, width - name_w - title_w - status_w - 3)
    for name, title, path, status in rows:
        print(f"{name:<{name_w}} {title:<{title_w}} {elide(path, path_w):<{path_w}} {status}".rstrip())
    for path, reason in broken:
        print(f"{path}: {reason}")
    return len(broken)


def elide(path: str, width: int) -> str:
    """Keep both ends. Dropping the front alone left the useless middle of a
    long temp path while losing the workspace that identifies it."""
    if len(path) <= width:
        return path
    keep = width - 1
    return path[: keep // 3] + "…" + path[-(keep - keep // 3):]


def _canon(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path
