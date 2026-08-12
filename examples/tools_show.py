"""Tool usage — a bar chart, deliberately not another table.

Bars, not rows of numbers, because the question is proportion: Bash being
5x everything else is the shape you want to see instantly. Failures are the
red tail of each bar, so a tool that fails often can't hide behind a big
count.
"""

from rich.text import Text

from tartifacts import app, widgets

SCALES = ["24h", "7d"]
BAR, FAIL = "█", "▓"


def data(state):
    return state.get("data") or {}


def window(state):
    return data(state).get("windows", {}).get(state.get("scale", "24h"), {})


def tools(state):
    found = window(state).get("tools", [])
    if state.get("errors_only"):
        return [t for t in found if t["errors"]]
    return found


def rate(entry):
    return 100 * entry["errors"] / entry["calls"] if entry["calls"] else 0.0


def bar_row(entry, biggest, width, name_width):
    """One tool: name, proportional bar with its failures as a red tail."""
    span = max(1, int(round(width * entry["calls"] / biggest))) if biggest else 0
    bad = min(span, int(round(span * entry["errors"] / entry["calls"]))) if entry["calls"] else 0
    if entry["errors"] and bad == 0:
        bad = 1                      # never round a real failure away
    hot = rate(entry) >= 10
    return Text.assemble(
        (f"{widgets.plain(entry['tool'])[:name_width]:<{name_width}} ", ""),
        (BAR * (span - bad), "cyan"),
        (FAIL * bad, "bold red"),
        (f"  {entry['calls']:>5}", "bold"),
        (f"  {rate(entry):>4.0f}% fail", "bold red" if hot else "dim"),
    )


def repo_row(entry, biggest, width, name_width):
    span = max(1, int(round(width * entry["calls"] / biggest))) if biggest else 0
    return Text.assemble(
        (f"{widgets.plain(entry['repo'])[:name_width]:<{name_width}} ", "dim"),
        (BAR * span, "magenta"),
        (f"  {entry['calls']:>5}", ""),
        (f"  {entry['errors']:>3} failed", "red" if entry["errors"] else "dim"),
    )


KEYS = widgets.Keys({
    "t": ("timescale", lambda st: st.update(
        scale=SCALES[(SCALES.index(st.get("scale", "24h")) + 1) % len(SCALES)])),
    "e": ("errors only", lambda st: st.update(errors_only=not st.get("errors_only"))),
})


def render(state, console):
    current = window(state)
    shown = tools(state)
    repos = current.get("repos", [])
    calls, errors = current.get("calls", 0), current.get("errors", 0)
    name_width = 14
    span = max(20, console.size.width - name_width - 26)

    head = widgets.header(
        Text.assemble(
            ("Tool usage", "bold"),
            ("   window ", "dim"),
            (state.get("scale", "24h"), "cyan"),
            ("   errors only" if state.get("errors_only") else "", "yellow"),
        ),
        warn=bool(errors),
        warning=f"{errors} call{'s' if errors != 1 else ''} failed",
    )
    kpis = widgets.kpi_row([
        ("tool calls", f"{calls:,}"),
        ("failed", f"{errors:,}"),
        ("failure rate", f"{(100 * errors / calls) if calls else 0:.1f}%"),
        ("distinct tools", str(len(current.get("tools", [])))),
    ])
    keys = widgets.help_line(KEYS.help)
    note = Text.from_markup(
        f"{BAR} calls   [red]{FAIL}[/red] failures — a tool's error rate only exists by "
        "joining tool_use to its tool_result, which is why nothing else shows it",
        style="dim italic",
    )
    # Bars are a fixed stack, so they have to be budgeted by hand: split the
    # rows left between the two charts rather than overflowing the pane.
    room = widgets.remaining_height(console, head, kpis, note, keys, 3)
    for_tools = max(1, min(len(shown), room - min(len(repos), 4) - 1))
    for_repos = max(0, room - for_tools - 1)

    biggest = max((t["calls"] for t in shown), default=1)
    chart = (
        widgets.stack(*[bar_row(t, biggest, span, name_width) for t in shown[:for_tools]])
        if shown else Text("no tool calls in this window", style="green")
    )
    by_repo = max((r["calls"] for r in repos), default=1)
    repo_chart = widgets.stack(
        Text("by repo", style="dim"),
        *[repo_row(r, by_repo, span, name_width) for r in repos[:for_repos]],
    ) if for_repos else None
    return widgets.stack(head, kpis, chart, Text(""), repo_chart, note, keys)


app.run(
    render=render,
    state={"scale": "24h", "errors_only": False},
    keys=KEYS,
    summary=lambda st: {
        "scale": st.get("scale"),
        "calls": window(st).get("calls"),
        "errors": window(st).get("errors"),
        "worst": [
            {"tool": t["tool"], "fail_percent": round(rate(t), 1)}
            for t in sorted(window(st).get("tools", []), key=lambda e: -rate(e))[:3]
            if t["errors"]
        ],
    },
)
