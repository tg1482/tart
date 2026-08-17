"""Token burn — where the last 24 hours of Claude actually went.

Two sparklines (tokens and dollars per hour) over a per-model and
per-project split. The point is the SHAPE: a flat line with a spike at 3pm
is a different problem from a steady grind.
"""

import datetime

from rich.panel import Panel
from rich.text import Text

from tartifacts import app, widgets

BAR_WIDTH = 24


def compact(n):
    """291715899 -> 291.7M. Token counts are read at a glance."""
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return str(int(n))


def data(state):
    return state.get("data") or {}


def countdown(iso):
    """'2h 14m' until an ISO reset stamp, or '' when there isn't one."""
    if not iso:
        return ""
    try:
        left = datetime.datetime.fromisoformat(iso) - datetime.datetime.now(datetime.timezone.utc)
    except ValueError:
        return ""
    minutes = max(0, int(left.total_seconds() // 60))
    if minutes >= 1440:
        return f"{minutes // 1440}d {minutes % 1440 // 60}h"
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def severity(percent):
    return "green" if percent < 50 else "yellow" if percent < 80 else "red"


def gauge(label, percent, note):
    """A labelled bar. Percent is all the API gives — there are no dollars."""
    filled = int(round(BAR_WIDTH * min(percent, 100) / 100))
    style = severity(percent)
    return Text.assemble(
        (f"{label:<16}", "bold"),
        ("█" * filled, style),
        ("─" * (BAR_WIDTH - filled), "bright_black"),
        (f" {percent:>3.0f}%", f"bold {style}"),
        (f"  resets in {note}" if note else "", "dim"),
    )


def limits_panel(state):
    """None when there is no Keychain to read, so the panel simply vanishes."""
    usage = data(state).get("plan_usage")
    if not usage or not usage.get("windows"):
        return None
    lines = [gauge(w["name"], w["percent"], countdown(w.get("resets_at")))
             for w in usage["windows"]]
    extra = usage.get("extra") or {}
    if extra.get("enabled"):
        lines.append(Text.assemble(
            (f"{'extra credits':<16}", "bold"),
            (f"{extra.get('used', 0):,.0f} of {extra.get('limit', 0):,}", "cyan"),
        ))
    elif extra.get("reason"):
        lines.append(Text(f"{'extra credits':<16}off ({extra['reason']})", style="dim"))
    worst = max((w["percent"] for w in usage["windows"]), default=0)
    plan = usage.get("plan") or "plan"
    return Panel(widgets.stack(*lines), title=f"Limits · {plan}",
                 border_style=severity(worst))


KEYS = widgets.Keys({
    "d": ("detail", lambda st: st.update(detail=not st.get("detail", False))),
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
    limits = limits_panel(state)

    # Limits are the question you open this for; spend is the follow-up.
    if not state.get("detail"):
        return widgets.stack(
            widgets.header(Text.assemble(("Claude limits", "bold"),
                                         ("   ·   d for spend detail", "dim"))),
            limits or widgets.header("no plan data — press d for local spend", warn=True),
            widgets.help_line("d detail"),  # sorting needs the table, which is hidden
        )

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
        height=widgets.remaining_height(console, head, limits, kpis, trends, models, keys, 4),
        console=console,
        title=f"By project — sorted by {state.get('by', 'tokens')}",
        empty="no Claude activity in the window",
    )
    return widgets.stack(head, limits, kpis, trends, models, table, keys)


app.run(
    render=render,
    state={"cursor": widgets.Cursor(), "by": "tokens", "detail": False},
    keys=KEYS,
    rows=projects,
    summary=lambda st: {
        "limits": (data(st).get("plan_usage") or {}).get("windows"),
        "window_hours": data(st).get("window_hours"),
        "totals": data(st).get("totals"),
        "top_project": (projects(st) or [{}])[0].get("project"),
    },
)
