"""tartifacts — live terminal artifacts.

A live terminal artifact is a dashboard that stays open, keeps its own data
fresh, and stays findable afterwards. This package is the loop around one,
so you only write the dashboard.

Not a renderer: you write `render(state, console)` with `rich` directly,
however yours needs to look. This package owns the parts every artifact
needs and none should reimplement — raw-mode terminal setup and teardown,
reactive file/command polling, refresh from declared staleness, discovery
from any directory, and headless `--once`/`--json` so an agent can read
what it built.

The command you type is `tart`. See `tart --skill` for the full reference.
"""

# The public surface for fetch scripts. `current_manifest`, not `manifest`,
# because binding that name here would shadow the `tartifacts.manifest`
# module for anyone importing it — which is exactly what the rename to
# `manifest` first did.
from .manifest import current as current_manifest, data_path, write_data

__all__ = ["current_manifest", "data_path", "write_data"]
