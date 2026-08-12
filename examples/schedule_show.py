"""Scheduled jobs — what runs behind your back, and what's been failing.

Yours sort above Apple's, and failures above successes, because the whole
point is the job you installed six months ago that has been exiting 1 ever
since. Press `a` to show or hide Apple's own agents.
"""

from rich.text import Text

from tartifacts import app, widgets

MARK = {
    "failed": ("✗", "bold red"),
    "signal": ("✗", "red"),
    "running": ("●", "green"),
    "scheduled": ("◷", "cyan"),
    "ok": ("·", "dim"),
}


def data(state):
    return state.get("data") or {}


def jobs(state):
    everything = data(state).get("jobs", [])
    if not state.get("show_apple"):
        everything = [job for job in everything if not job["apple"]]
    if state.get("errors_only"):
        everything = [job for job in everything if job["kind"] in ("failed", "signal")]
    return everything


def row(job):
    mark, style = MARK.get(job["kind"], ("?", "dim"))
    name = job["label"] if job["source"] == "launchd" else job["label"][:70]
    return [
        Text(mark, style=style),
        Text(job["source"], style="dim"),
        Text(widgets.plain(name), style="" if job["yours"] else "dim"),
        Text(widgets.plain(job["detail"]), style=style if job["kind"] in ("failed", "signal") else "dim"),
    ]


KEYS = widgets.Keys({
    "a": ("apple agents", lambda st: st.update(show_apple=not st.get("show_apple"))),
    "e": ("errors only", lambda st: st.update(errors_only=not st.get("errors_only"))),
})


def render(state, console):
    payload = data(state)
    counts = payload.get("counts", {})
    shown = jobs(state)
    broken = [j for j in shown if j["kind"] in ("failed", "signal")]

    head = widgets.header(
        Text.assemble(
            ("Scheduled jobs", "bold"),
            ("   launchd + cron", "dim"),
            ("   ·   apple agents ", "dim"),
            ("shown" if state.get("show_apple") else "hidden", "cyan"),
            ("   ·   errors only" if state.get("errors_only") else "", "yellow"),
        ),
        warn=bool(counts.get("broken_yours")),
        warning=f"{counts.get('broken_yours')} of your own jobs are failing",
    )
    kpis = widgets.kpi_row([
        ("jobs", str(counts.get("total", 0))),
        ("running", str(counts.get("running", 0))),
        ("not ok", str(counts.get("broken", 0))),
        ("yours failing", str(counts.get("broken_yours", 0))),
    ])
    keys = widgets.help_line(widgets.Cursor.KEYS, KEYS.help)
    note = Text(
        f"{len(broken)} of the {len(shown)} shown last exited badly — "
        "column 2 of `launchctl list`, which nothing else surfaces",
        style="dim italic",
    )
    table = widgets.scrolling_table(
        shown,
        state["cursor"],
        [
            widgets.Column("", width=2),
            widgets.Column("via", width=8, drop_below=90),
            widgets.Column("job"),
            widgets.Column("last result", width=22),
        ],
        row,
        height=widgets.remaining_height(console, head, kpis, note, keys),
        console=console,
        title="Jobs",
        empty="nothing scheduled",
    )
    return widgets.stack(head, kpis, table, note, keys)


app.run(
    render=render,
    state={"cursor": widgets.Cursor(), "show_apple": False, "errors_only": False},
    rows=jobs,
    keys=KEYS,
    summary=lambda st: {
        "counts": data(st).get("counts"),
        "failing": [j["label"] for j in data(st).get("jobs", [])
                    if j["kind"] in ("failed", "signal") and j["yours"]],
    },
)
