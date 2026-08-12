"""Airwaves — signal, latency, and every battery in the room.

RSSI is the number nobody can read, so it's shown as bars with the actual
dBm beside it: -55 is excellent, -75 is where video calls start breaking up,
and knowing which one you're on is the whole point.
"""

from rich.text import Text

from tartifacts import app, widgets

# dBm ceiling -> (label, style). Anything worse than the last is "unusable".
SIGNAL = [(-60, "excellent", "bold green"), (-67, "good", "green"),
          (-72, "fair", "yellow"), (-80, "weak", "red")]


def data(state):
    return state.get("data") or {}


KEYS = widgets.Keys({
    "e": ("low battery only", lambda st: st.update(low=not st.get("low"))),
})


def devices(state):
    found = data(state).get("bluetooth", [])
    if state.get("low"):
        return [d for d in found if (d["battery"] or 100) <= 40]
    return found


def rate(rssi):
    if rssi is None:
        return "unknown", "dim", 0
    for ceiling, label, style in SIGNAL:
        if rssi >= ceiling:
            return label, style, max(1, int((rssi + 100) / 45 * 10))
    return "unusable", "bold red", 1


def signal_bar(rssi):
    label, style, bars = rate(rssi)
    return Text.assemble(
        ("▁▃▅▆█"[: min(5, max(1, bars // 2))], style),
        ("▁▃▅▆█"[min(5, max(1, bars // 2)):], "dim"),
        (f"  {rssi if rssi is not None else '?'} dBm", style),
        (f"  {label}", "dim"),
    )


def battery_cell(device):
    level = device["battery"]
    if level is None:
        pair = [device.get("left"), device.get("right")]
        if any(pair):
            return Text(f"L {pair[0] or '?'}  R {pair[1] or '?'}", style="cyan")
        return Text("—", style="dim")
    style = "red" if level <= 20 else ("yellow" if level <= 40 else "green")
    filled = max(0, min(10, round(level / 10)))
    return Text.assemble(("█" * filled, style), ("█" * (10 - filled), "dim"), (f" {level}%", style))


def row(device):
    return [
        Text(widgets.plain(device["name"])),
        Text(widgets.plain(device["kind"]), style="dim"),
        battery_cell(device),
    ]


def render(state, console):
    payload = data(state)
    wifi, link = payload.get("wifi", {}), payload.get("link", {})
    history = payload.get("history", [])
    low = [d for d in devices(state) if (d["battery"] or 100) <= 20]

    head = widgets.header(
        Text.assemble(
            (widgets.plain(wifi.get("ssid") or "not connected"), "bold"),
            ("   ", ""),
            (f"{wifi.get('channel') or '?'}", "dim"),
            ("   ", ""),
            (f"{wifi.get('rate') or '?'} Mbps", "dim"),
        ),
        warn=bool(low),
        warning=f"{len(low)} device{'s' if len(low) != 1 else ''} below 20%",
    )
    kpis = widgets.kpi_row([
        ("signal", f"{wifi.get('rssi') or '?'} dBm"),
        ("gateway latency", f"{link.get('latency_ms') or '?'} ms"),
        ("packet loss", f"{link.get('loss_percent', 0) or 0:.0f}%"),
        ("this mac", link.get("ip") or "?"),
    ])
    radio = widgets.stack(
        Text.assemble(("Signal   ", "dim"), signal_bar(wifi.get("rssi"))),
        Text.assemble(
            ("Link     ", "dim"),
            (f"{wifi.get('security') or '?'}", ""),
            ("  gateway ", "dim"), (link.get("gateway") or "?", ""),
            ("  on ", "dim"), (wifi.get("interface") or "?", ""),
        ),
    )
    trends = widgets.stack(
        widgets.trend([h.get("rssi") for h in history], label="Signal  ", style="cyan"),
        widgets.trend([h.get("latency") for h in history], label="Latency ", style="magenta"),
    )
    keys = widgets.help_line(widgets.Cursor.KEYS, KEYS.help)
    table = widgets.scrolling_table(
        devices(state),
        state["cursor"],
        [
            widgets.Column("device", width=28),
            widgets.Column("kind", width=14, drop_below=80),
            widgets.Column("battery"),
        ],
        row,
        height=widgets.remaining_height(console, head, kpis, radio, trends, keys),
        console=console,
        title="Bluetooth",
        empty="nothing connected",
    )
    return widgets.stack(head, kpis, radio, trends, table, keys)


app.run(
    render=render,
    state={"cursor": widgets.Cursor(), "low": False},
    keys=KEYS,
    rows=devices,
    summary=lambda st: {
        "ssid": (data(st).get("wifi") or {}).get("ssid"),
        "rssi": (data(st).get("wifi") or {}).get("rssi"),
        "latency_ms": (data(st).get("link") or {}).get("latency_ms"),
        "low_battery": [d["name"] for d in devices(st) if (d["battery"] or 100) <= 20],
    },
)
