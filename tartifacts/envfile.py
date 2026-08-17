"""KEY=VALUE files a manifest's commands need — systemd's `EnvironmentFile=`.

The same fetch runs in three different environments: your interactive
shell, the background keeper, and cron. Your shell has your exports and
your dotfiles; the other two have almost nothing — which is how a fetch
works when you run it and fails all night with `uv: command not found` or
a missing API key. Loading a declared file at the one layer that owns
process spawning gives all three the same environment.

This is not a secrets manager: no encryption, no templating, no keychain.
Just KEY=VALUE lines loaded into the child's environment, kept out of the
manifest itself (which gets committed) and out of the command string
(which shows in `ps`).

Values from the file OVERRIDE the inherited environment — determinism is
the point, and "my shell happened to export a stale one" is exactly the
drift this exists to end.
"""

from __future__ import annotations

from pathlib import Path


def load(path: Path) -> dict[str, str]:
    """Raises OSError when the file is missing or unreadable — a declared
    env_file that can't be loaded must be loud, because the failure it
    causes downstream (an API rejecting a blank key) points anywhere but
    here."""
    return parse(Path(path).read_text())


def parse(text: str) -> dict[str, str]:
    """The common ground of .env dialects, nothing more: KEY=VALUE, blank
    lines and `#` comments skipped, an optional `export ` prefix tolerated
    (so an existing shell-sourced secrets file works unchanged), matching
    surrounding quotes stripped. No interpolation, no multi-line values."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if not key or " " in key:
            continue  # not a variable assignment — some other shell construct
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values
