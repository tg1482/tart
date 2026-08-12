"""Machine vitals — battery, load, memory, disk, and where they're heading.

The sparklines are the reason this exists. "Memory 71%" is a number; memory
climbing 40 -> 71 over an hour is a leak, and only the second one tells you
to go looking.
"""

from rich.text import Text

from tartifacts import app, fmt, widgets

BAR = "█"


def data(state):
    return state.get("data") or {}


def series(state, key):
    return [point.get(key) for point in data(state).get("history", [])]


def gauge(percent, width=24, good_low=True):
    """A filled bar that turns red as it fills (or empties, for battery)."""
    percent = max(0.0, min(100.0, float(percent or 0)))
    filled = int(round(width * percent / 100))
    danger = percent >= 85 if good_low else percent <= 20
    warn = percent >= 70 if good_low else percent <= 40
    style = "red" if danger else ("yellow" if warn else "green")
    return Text.assemble(
        (BAR * filled, style), (BAR * (width - filled), "dim"), (f" {percent:.0f}%", style)
    )


def power_line(payload):
    battery = payload.get("battery", {})
    icon = {"charged": "⚡", "charging": "⚡", "battery": "▮"}.get(battery.get("state"), "▮")
    bits = [
        Text.assemble((f"{icon} ", "bold"), gauge(battery.get("percent"), good_low=False)),
        Text.assemble(("  health ", "dim"), (f"{battery.get('health') or '?'}%", "")),
        Text.assemble(("  cycles ", "dim"), (str(battery.get("cycles") or "?"), "")),
    ]
    if battery.get("temp_c"):
        bits.append(Text.assemble(("  ", "dim"), (f"{battery['temp_c']}°C", "dim")))
    if battery.get("remaining") and battery["remaining"] != "0:00":
        bits.append(Text.assemble(("  ", "dim"), (f"{battery['remaining']} left", "cyan")))
    return Text.assemble(*[part for bit in bits for part in (bit, "")])


KEYS = widgets.Keys({
    "s": ("sort cpu/mem", lambda st: st.update(
        by="mem" if st.get("by", "cpu") == "cpu" else "cpu")),
})


def procs(state):
    found = data(state).get("processes", [])
    return sorted(found, key=lambda p: -p[state.get("by", "cpu")])


def proc_row(entry):
    heat = "red" if entry["cpu"] >= 80 else ("yellow" if entry["cpu"] >= 30 else "")
    return [
        Text(str(entry["pid"]), justify="right", style="dim"),
        Text(widgets.plain(entry["name"])),
        Text(f"{entry['cpu']:.1f}", justify="right", style=heat),
        Text(f"{entry['mem']:.1f}", justify="right", style="dim"),
    ]


def render(state, console):
    payload = data(state)
    memory, disk = payload.get("memory", {}), payload.get("disk", {})
    load = payload.get("load", [0, 0, 0])
    cores = payload.get("cores", 1)

    head = widgets.header(
        Text.assemble(
            (payload.get("host", "this mac"), "bold"),
            ("   up ", "dim"),
            (payload.get("uptime", "?"), ""),
            (f"   {cores} cores", "dim"),
        )
    )
    kpis = widgets.kpi_row([
        ("load", f"{load[0]:.2f}"),
        ("memory", f"{memory.get('percent', 0):.0f}%"),
        ("disk free", fmt.size(disk.get("free", 0))),
        ("battery", f"{(payload.get('battery') or {}).get('percent', 0)}%"),
    ])
    gauges = widgets.stack(
        power_line(payload),
        Text.assemble(("CPU  ", "dim"), gauge(100 * load[0] / cores),
                      ("   1/5/15m ", "dim"),
                      (f"{load[0]:.2f} {load[1]:.2f} {load[2]:.2f}", "dim")),
        Text.assemble(("RAM  ", "dim"), gauge(memory.get("percent", 0)),
                      ("   ", ""), (fmt.size(memory.get("used", 0)), "dim"),
                      (" of ", "dim"), (fmt.size(memory.get("total", 0)), "dim")),
        Text.assemble(("SSD  ", "dim"), gauge(disk.get("percent", 0)),
                      ("   ", ""), (fmt.size(disk.get("free", 0)), "dim"),
                      (" free", "dim")),
    )
    trends = widgets.stack(
        widgets.trend(series(state, "load"), label="CPU %   ", style="cyan"),
        widgets.trend(series(state, "memory"), label="Memory %", style="magenta"),
        widgets.trend(series(state, "battery"), label="Battery ", style="green"),
    )
    samples = len(payload.get("history", []))
    note = Text(
        f"history: {samples} sample{'s' if samples != 1 else ''} — the artifact "
        "accumulates its own, one per fetch",
        style="dim italic",
    )
    keys = widgets.help_line(widgets.Cursor.KEYS, KEYS.help)
    table = widgets.scrolling_table(
        procs(state),
        state["cursor"],
        [
            widgets.Column("pid", width=7, justify="right"),
            widgets.Column("process"),
            widgets.Column("cpu %", width=8, justify="right"),
            widgets.Column("mem %", width=8, justify="right"),
        ],
        proc_row,
        height=widgets.remaining_height(console, head, kpis, gauges, trends, note, keys),
        console=console,
        title=f"Busiest processes — by {state.get('by', 'cpu')}",
        empty="nothing measured yet",
    )
    return widgets.stack(head, kpis, gauges, trends, table, note, keys)


app.run(
    render=render,
    state={"cursor": widgets.Cursor(), "by": "cpu"},
    keys=KEYS,
    rows=procs,
    summary=lambda st: {
        "load": data(st).get("load"),
        "memory_percent": (data(st).get("memory") or {}).get("percent"),
        "disk_free": (data(st).get("disk") or {}).get("free"),
        "battery": data(st).get("battery"),
    },
)
