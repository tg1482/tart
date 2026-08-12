"""Pulse — 48 hours of Claude activity, one strip per project.

A heatmap made of `timeline` strips. Gaps are the signal: a project with a
solid bar all night is one you left running, and a row of dots at 3am is a
project nobody has touched since Tuesday.
"""

import time

from rich.text import Text

from tartifacts import app, widgets

# Turns-per-hour thresholds -> colour. Tuned so an ordinary working hour is
# cyan and a genuinely hot hour stands out rather than saturating.
HEAT = [(60, "bold magenta"), (30, "bold red"), (15, "yellow"), (5, "cyan"), (1, "blue")]


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


def data(state):
    return state.get("data") or {}


KEYS = widgets.Keys({
    "s": ("sort busy/name", lambda st: st.update(
        by="name" if st.get("by", "busy") == "busy" else "busy")),
})


def rows(state):
    found = data(state).get("projects", [])
    if state.get("by") == "name":
        return sorted(found, key=lambda p: p["project"])
    return sorted(found, key=lambda p: -p["turns"])


def heat(count):
    if not count:
        return "dim"
    for floor, style in HEAT:
        if count >= floor:
            return style
    return "blue"


def strip(project, hours, width_for_label):
    name = widgets.plain(project)[:width_for_label].ljust(width_for_label)
    return widgets.timeline(
        [count or None for count in hours],
        heat,
        left_label=name,
        right_label=f"{sum(hours):>5}",
    )


def render(state, console):
    payload = data(state)
    projects = rows(state)
    labels = payload.get("hour_labels", [])
    window = payload.get("window_hours", 48)

    head = widgets.header(
        Text.assemble(
            ("Claude pulse", "bold"),
            (f"   {window}h · {payload.get('turns', 0):,} turns · ", "dim"),
            (f"{len(projects)} projects", "dim"),
        )
    )
    kpis = widgets.kpi_row([
        ("turns", f"{payload.get('turns', 0):,}"),
        ("projects", str(len(projects))),
        ("busiest hour", f"{max(payload.get('total_by_hour') or [0]):,} turns"),
        ("data age", ago(payload.get("at"))),
    ])
    everything = widgets.timeline(
        [count or None for count in payload.get("total_by_hour", [])],
        heat,
        left_label="ALL      ",
        right_label=f"{payload.get('turns', 0):>5}",
        title=f"All projects — {labels[0] if labels else ''}h to {labels[-1] if labels else ''}h",
    )
    legend = Text.assemble(
        ("turns/hour  ", "dim italic"),
        *[part for floor, style in reversed(HEAT)
          for part in ((" █", style), (f" {floor}+ ", "dim"))],
        ("  · quiet", "dim"),
    )
    keys = widgets.help_line(KEYS.help)

    # Each project strip is two rows: the bar, then its label line.
    room = widgets.remaining_height(console, head, kpis, everything, legend, keys, 2)
    shown = projects[: max(1, room // 2)]
    width = max((len(p["project"]) for p in shown), default=8)
    width = min(width, 22)
    bars = widgets.stack(*[strip(p["project"], p["hours"], width) for p in shown])
    return widgets.stack(head, kpis, everything, bars, legend, keys)


app.run(
    render=render,
    rows=rows,
    state={"by": "busy"},
    keys=KEYS,
    summary=lambda st: {
        "window_hours": data(st).get("window_hours"),
        "turns": data(st).get("turns"),
        "projects": [p["project"] for p in rows(st)][:10],
    },
)
