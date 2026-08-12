"""Snapshot every Claude herdr knows about, into the artifact's data file.

`herdr agent list` already returns JSON; this keeps only the fields the
dashboard draws, so the data file stays readable and the render stays dumb.
"""

import json
import os
import subprocess
import time

import tartifacts


def agents() -> list[dict]:
    out = subprocess.run(
        ["herdr", "agent", "list"], capture_output=True, text=True, timeout=15
    )
    if out.returncode != 0:
        raise SystemExit(f"herdr agent list failed: {out.stderr.strip()}")
    found = json.loads(out.stdout)["result"]["agents"]
    return [
        {
            "status": a.get("agent_status") or "unknown",
            "repo": os.path.basename(a.get("cwd") or "") or "~",
            "cwd": a.get("cwd") or "",
            "doing": a.get("terminal_title_stripped") or "",
            "pane": a.get("pane_id") or "",
            "tab": a.get("tab_id") or "",
            "focused": bool(a.get("focused")),
            "foreground": a.get("foreground_cwd") or "",
            "seq": a.get("state_change_seq") or 0,
        }
        for a in found
    ]


found = sorted(agents(), key=lambda a: (a["status"] != "working", a["repo"]))
tartifacts.write_data({"agents": found, "at": time.time()})
print(f"{len(found)} agents")
