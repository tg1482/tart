"""Where tart keeps its own state.

One resolver, in one place. Four modules each calling `Path.home()`
independently is how a new module silently escapes test isolation and
writes to the real `~/.tart` — the tests had to monkeypatch every constant
by name, so anything added later was unprotected by default.

`TART_HOME` also lets you run an isolated config: a scratch workspace, a
second set of roots, a reproduction of someone else's setup.

Deliberately NOT XDG-aware. `XDG_STATE_HOME` is set on plenty of machines,
and honouring it would relocate an existing `~/.tart` out from under its
owner — every root unregistered, every artifact "not found", with no
obvious cause. A single explicit override is the whole benefit without that.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "TART_HOME"
DEFAULT = Path.home() / ".tart"


def home() -> Path:
    override = os.environ.get(ENV_VAR)
    return Path(override).expanduser() if override else DEFAULT
