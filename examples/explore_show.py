"""explore — navigate directories, sortable, with a one-key Claude split.

A live directory listing rather than a file manager: name/size/age
columns, sort cycling, drill in and out — and `c` opens a fresh Claude
Code instance in a new herdr split at the selected directory. For real
file management (previews, bulk ops, search) use a real file manager;
this stays a dashboard with one very good button.

The listing is cached per directory and invalidated by the directory's
own mtime, so a file appearing or vanishing shows up on the next tick
without any fetch — the directory IS the data file.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from rich.text import Text

from tartifacts import app, fmt, widgets

START = os.environ.get("TART_EXPLORE_START") or str(Path.home())
SORTS = ("name", "size", "modified")

_cache: dict = {}  # cwd -> (dir mtime, show_hidden, entries)


def listing(state) -> list[dict]:
    cwd = state.get("cwd") or START
    hidden = bool(state.get("show_hidden"))
    try:
        stamp = os.path.getmtime(cwd)
    except OSError:
        return []
    cached = _cache.get(cwd)
    if cached and cached[0] == stamp and cached[1] == hidden:
        entries = cached[2]
    else:
        entries = []
        try:
            with os.scandir(cwd) as it:
                for entry in it:
                    if not hidden and entry.name.startswith("."):
                        continue
                    try:
                        info = entry.stat()
                    except OSError:
                        continue
                    entries.append({
                        "name": entry.name,
                        "dir": entry.is_dir(follow_symlinks=False),
                        "size": info.st_size,
                        "mtime": info.st_mtime,
                    })
        except OSError:
            return []
        _cache.clear()          # one directory at a time; no reason to hoard
        _cache[cwd] = (stamp, hidden, entries)

    key = state.get("sort", "name")
    if key == "size":
        entries = sorted(entries, key=lambda e: e["size"], reverse=True)
    elif key == "modified":
        entries = sorted(entries, key=lambda e: e["mtime"], reverse=True)
    else:
        entries = sorted(entries, key=lambda e: e["name"].lower())
    return sorted(entries, key=lambda e: not e["dir"])   # dirs first, stable


def target_dir(state) -> str:
    """Where `c` should open Claude: the selected directory, or the cwd
    when a file (or nothing) is selected."""
    chosen = state["cursor"].selected(listing(state))
    cwd = state.get("cwd") or START
    if chosen and chosen["dir"]:
        return os.path.join(cwd, chosen["name"])
    return cwd


def spawn_split(state, command: str, cwd: str, label: str) -> None:
    """Split the current herdr pane and run `command` in it, focused.

    Every outcome lands in the footer — the devbox tart once piped its
    action to DEVNULL and reported success unconditionally, which is how
    an action gets built so it can only ever look like it worked."""
    pane = os.environ.get("HERDR_PANE_ID")
    if not pane:
        state["flash"] = "not inside herdr — no pane to split"
        return
    try:
        split = subprocess.run(
            ["herdr", "pane", "split", pane, "--direction", "right",
             "--cwd", cwd, "--focus"],
            capture_output=True, text=True, timeout=10,
        )
        match = re.search(r'"pane_id":"([^"]+)"', split.stdout)
        if split.returncode != 0 or not match:
            state["flash"] = f"split failed: {(split.stderr or split.stdout).strip()[:80]}"
            return
        new_pane = match.group(1)
        ran = subprocess.run(
            ["herdr", "pane", "run", new_pane, command],
            capture_output=True, text=True, timeout=10,
        )
        if ran.returncode != 0:
            state["flash"] = f"{label} launch failed: {ran.stderr.strip()[:80]}"
            return
        state["flash"] = f"{label} → {new_pane}"
    except (OSError, subprocess.TimeoutExpired) as bad:
        state["flash"] = f"herdr unreachable: {bad}"


def spawn_claude(state) -> None:
    where = target_dir(state)
    spawn_split(state, "claude", where, f"claude in {where}")


def spawn_editor(state) -> None:
    """Open the selection — file OR directory — in the editor. nvim, vim,
    emacs and `code` all accept a directory (netrw/oil, dired, workspace);
    nano and micro don't, so the target is passed as-is and the editor
    gets to have its own opinion in its own pane."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nvim"
    chosen = state["cursor"].selected(listing(state))
    cwd = state.get("cwd") or START
    target = os.path.join(cwd, chosen["name"]) if chosen else cwd
    workdir = target if chosen and chosen["dir"] else cwd
    spawn_split(state, f"{editor} {shlex.quote(target)}", workdir,
                f"{os.path.basename(editor.split()[0])} {os.path.basename(target)}")


def descend(state) -> None:
    chosen = state["cursor"].selected(listing(state))
    if chosen and chosen["dir"]:
        state["cwd"] = os.path.join(state.get("cwd") or START, chosen["name"])
        state["cursor"].index = 0
        state["flash"] = None


def ascend(state) -> None:
    state["cwd"] = os.path.dirname(state.get("cwd") or START) or "/"
    state["cursor"].index = 0
    state["flash"] = None


KEYS = widgets.Keys({
    "s": ("sort", lambda st: st.update(
        sort=SORTS[(SORTS.index(st.get("sort", "name")) + 1) % len(SORTS)])),
    ".": ("hidden", lambda st: st.update(show_hidden=not st.get("show_hidden"))),
    "c": ("claude here", spawn_claude),
    # `v` rather than the file-manager-conventional `e`: tart's own key
    # conventions reserve e for "errors only", and v reads as vim — which
    # is what VISUAL/EDITOR-unset machines get.
    "v": ("vim", spawn_editor),
})

COLUMNS = [
    widgets.Column("Name"),
    widgets.Column("Size", width=9, justify="right"),
    widgets.Column("Modified", width=9, justify="right"),
]


def row(entry) -> list:
    name = entry["name"] + ("/" if entry["dir"] else "")
    style = "bold cyan" if entry["dir"] else ""
    return [
        Text(name, style=style),
        "-" if entry["dir"] else fmt.size(entry["size"]),
        fmt.age(time.time() - entry["mtime"]),
    ]


def on_key(key, state) -> None:
    if key in ("\r", "\n", "l", "\x1b[C"):     # enter / l / right arrow
        descend(state)
    elif key in ("h", "\x7f", "\x1b[D"):       # h / backspace / left arrow
        ascend(state)


def render(state, console):
    entries = listing(state)
    cwd = state.get("cwd") or START
    dirs = sum(1 for e in entries if e["dir"])
    head = widgets.header(
        f"{cwd} · {dirs} dirs, {len(entries) - dirs} files · sort: {state.get('sort', 'name')}"
    )
    foot_parts = [widgets.Cursor.KEYS, KEYS.help, "→ enter · ← up"]
    foot = widgets.stack(
        Text(state["flash"], style="bold yellow") if state.get("flash") else None,
        widgets.help_line(*foot_parts),
    )
    return widgets.stack(
        head,
        widgets.scrolling_table(
            entries, state["cursor"], COLUMNS, row,
            height=widgets.remaining_height(console, head, foot),
            console=console, title="Contents", empty="empty directory",
        ),
        foot,
    )


def summary(state) -> dict:
    entries = listing(state)
    return {
        "cwd": state.get("cwd") or START,
        "dirs": sum(1 for e in entries if e["dir"]),
        "files": sum(1 for e in entries if not e["dir"]),
        "sort": state.get("sort", "name"),
    }


app.run(
    render=render,
    state={"cursor": widgets.Cursor(), "cwd": START, "sort": "name",
           "show_hidden": False, "flash": None},
    rows=listing,
    keys=KEYS,
    on_key=on_key,
    summary=summary,
)
