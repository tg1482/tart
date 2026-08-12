"""Claude swarm — every agent on this machine, what it's doing, who's blocked.

An idle agent is a *blocked* agent: it finished and is waiting on you. So
idle sorts to the top of the attention list, not the bottom, and the KPI
that matters is "how many are waiting", not "how many exist".
"""

import time

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from tartifacts import app, widgets

DOT = {"working": ("●", "bold green"), "idle": ("○", "yellow")}


def ago(at):
    """Human age of the data file's timestamp."""
    if not at:
        return "—"
    seconds = max(0.0, time.time() - at)
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


KEYS = widgets.Keys({
    "w": ("waiting only", lambda st: st.update(waiting=not st.get("waiting"))),
})


def agents(state):
    live = (state.get("data") or {}).get("agents", [])
    if state.get("waiting"):
        return [agent for agent in live if agent["status"] == "idle"]
    return live


def status_cell(agent):
    mark, style = DOT.get(agent["status"], ("?", "dim"))
    return Text(f"{mark} {agent['status']}", style=style)


def row(agent):
    focus = "▸" if agent["focused"] else " "
    return [
        status_cell(agent),
        Text(f"{focus} {widgets.plain(agent['repo'])}", style="bold" if agent["focused"] else ""),
        Text(widgets.plain(agent["doing"]), style="dim" if agent["status"] == "idle" else ""),
        Text(agent["pane"], style="dim"),
    ]


def detail(agent):
    if agent is None:
        return None
    mark, style = DOT.get(agent["status"], ("?", "dim"))
    lines = [
        Text.assemble(("status   ", "dim"), (f"{mark} {agent['status']}", style)),
        Text.assemble(("repo     ", "dim"), widgets.plain(agent["cwd"])),
        Text.assemble(("doing    ", "dim"), widgets.plain(agent["doing"]) or "—"),
        Text.assemble(("pane     ", "dim"), f"{agent['pane']}  (tab {agent['tab']})"),
    ]
    if agent["foreground"] and agent["foreground"] != agent["cwd"]:
        lines.append(Text.assemble(("shell in ", "dim"), widgets.plain(agent["foreground"])))
    return Panel(Group(*lines), title=f"Detail — {widgets.plain(agent['repo'])}", border_style=style)


def render(state, console):
    live = agents(state)
    working = [a for a in live if a["status"] == "working"]
    waiting = [a for a in live if a["status"] == "idle"]
    repos = {a["repo"] for a in live}

    head = widgets.header(
        Text.assemble(
            ("Claude swarm", "bold"),
            ("   ", ""),
            (f"{len(live)} agents across {len(repos)} repos", "dim"),
        )
    )
    kpis = widgets.kpi_row([
        ("working", str(len(working))),
        ("waiting on you", str(len(waiting))),
        ("repos", str(len(repos))),
        ("data age", ago((state.get("data") or {}).get("at"))),
    ])
    panel = detail(state["cursor"].selected(live))
    keys = widgets.help_line(widgets.Cursor.KEYS, KEYS.help)
    table = widgets.scrolling_table(
        live,
        state["cursor"],
        [
            widgets.Column("", width=11),
            widgets.Column("repo", width=26),
            widgets.Column("doing"),
            widgets.Column("pane", width=7, justify="right", drop_below=90),
        ],
        row,
        height=widgets.remaining_height(console, head, kpis, panel, keys),
        console=console,
        title="Agents",
        empty="no agents — is herdr running?",
    )
    return widgets.stack(head, kpis, table, panel, keys)


app.run(
    render=render,
    state={"cursor": widgets.Cursor(), "waiting": False},
    keys=KEYS,
    rows=agents,
    summary=lambda st: {
        "agents": len(agents(st)),
        "working": sum(1 for a in agents(st) if a["status"] == "working"),
        "waiting": sum(1 for a in agents(st) if a["status"] == "idle"),
        "repos": sorted({a["repo"] for a in agents(st)}),
    },
)
