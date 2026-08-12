"""Token burn — where the last 24 hours of Claude actually went.

Two sparklines (tokens and dollars per hour) over a per-model and
per-project split. The point is the SHAPE: a flat line with a spike at 3pm
is a different problem from a steady grind.
"""

from rich.text import Text

from tartifacts import app, widgets


def compact(n):
    """291715899 -> 291.7M. Token counts are read at a glance."""
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return str(int(n))


def data(state):
    return state.get("data") or {}


KEYS = widgets.Keys({
    "s": ("sort tokens/cost", lambda st: st.update(
        by="cost" if st.get("by", "tokens") == "tokens" else "tokens")),
})


def projects(state):
    found = data(state).get("projects", [])
    return sorted(found, key=lambda p: -p[state.get("by", "tokens")])


def money(value):
    return f"${value:,.2f}"


def model_line(entry):
    return Text.assemble(
        (f"{entry['model']:<9}", "bold"),
        (f"{entry['calls']:>6} turns  ", "dim"),
        (f"{compact(entry['input']):>8} in  ", ""),
        (f"{compact(entry['output']):>8} out  ", ""),
        (f"{money(entry['cost']):>9}", "bold green"),
    )


def row(entry):
    return [
        Text(widgets.plain(entry["project"])),
        Text(compact(entry["tokens"]), justify="right"),
        Text(money(entry["cost"]), justify="right", style="green"),
    ]


def render(state, console):
    payload = data(state)
    totals = payload.get("totals", {})
    per_hour = payload.get("per_hour", {})
    tokens, spend = per_hour.get("tokens", []), per_hour.get("cost", [])

    head = widgets.header(
        Text.assemble(
            ("Token burn", "bold"),
            ("   last ", "dim"),
            (f"{payload.get('window_hours', 24)}h", ""),
            ("   ·   prices are local estimates", "dim"),
        )
    )
    kpis = widgets.kpi_row([
        ("turns", f"{totals.get('calls', 0):,}"),
        ("output", compact(totals.get("output", 0))),
        ("cache read", compact(totals.get("cached", 0))),
        ("est. spend", money(totals.get("cost", 0.0))),
    ])
    trends = widgets.stack(
        widgets.trend(
            tokens,
            label="Tokens/h",
            style="cyan",
            summary=f"peak {compact(max(tokens or [0]))}",
        ),
        widgets.trend(
            spend,
            label="Cost/h  ",
            style="green",
            summary=f"peak {money(max(spend or [0]))}",
        ),
    )
    models = widgets.stack(*[model_line(m) for m in payload.get("models", [])])
    keys = widgets.help_line(widgets.Cursor.KEYS, KEYS.help)
    table = widgets.scrolling_table(
        projects(state),
        state["cursor"],
        [
            widgets.Column("project"),
            widgets.Column("tokens", width=10, justify="right"),
            widgets.Column("est. cost", width=11, justify="right"),
        ],
        row,
        height=widgets.remaining_height(console, head, kpis, trends, models, keys, 4),
        console=console,
        title=f"By project — sorted by {state.get('by', 'tokens')}",
        empty="no Claude activity in the window",
    )
    return widgets.stack(head, kpis, trends, models, table, keys)


app.run(
    render=render,
    state={"cursor": widgets.Cursor(), "by": "tokens"},
    keys=KEYS,
    rows=projects,
    summary=lambda st: {
        "window_hours": data(st).get("window_hours"),
        "totals": data(st).get("totals"),
        "top_project": (projects(st) or [{}])[0].get("project"),
    },
)
