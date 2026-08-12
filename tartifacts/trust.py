"""Which manifests you have agreed to execute.

`run` and `fetch` are shell commands, and tart finds manifests by scanning
configured roots — including, since the healing index landed, at any depth.
So cloning a repo is enough to put a `.tart/x.json` somewhere `tart run x`
will reach, and running it executes whatever that file says. `tart render`
reads like a read and executes too.

direnv answers this with one gate: an `.envrc` does nothing until you
`direnv allow` it. This is the same idea, keyed by **path and content
hash** rather than path alone, so editing a trusted manifest asks again —
trusting a file is not trusting whatever it later becomes.

mise took four CVEs in 2026 for variants of this, and the common thread in
all of them is a trust check placed after something that could already
execute. Hence the gate lives at the two points that actually run a
command (`cli._exec` and `cli._fetch`) rather than anywhere earlier:
loading a manifest is a plain JSON parse and runs nothing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import jsonfile, paths

TRUST_FILE = "trusted.json"


def trust_path() -> Path:
    return paths.home() / TRUST_FILE


def fingerprint(manifest_path: Path) -> str:
    """Hash of the file's bytes. Content, not mtime — a manifest restored
    from git or rewritten identically is still the thing you trusted."""
    return hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()


def _load() -> dict[str, str]:
    try:
        raw = json.loads(trust_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def is_trusted(manifest_path: Path) -> bool:
    """False when unknown, and false when the content has changed since you
    said yes."""
    try:
        resolved = str(Path(manifest_path).resolve())
        return _load().get(resolved) == fingerprint(manifest_path)
    except OSError:
        return False


def trust(manifest_path: Path) -> None:
    resolved = str(Path(manifest_path).resolve())
    jsonfile.write(trust_path(), {**_load(), resolved: fingerprint(manifest_path)})


def forget(manifest_path: Path) -> None:
    resolved = str(Path(manifest_path).resolve())
    remaining = {k: v for k, v in _load().items() if k != resolved}
    jsonfile.write(trust_path(), remaining)


def trusted_paths() -> list[str]:
    return sorted(_load())
