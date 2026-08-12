"""Disk — what's big, and how much of it is safe to delete.

Sorted by size, with the reclaimable ones marked. The headline number is
what you'd get back for free, because "70% full" makes you shop for a drive
and "31 GB of it is cache" makes you type one command.
"""

from rich.text import Text

from tartifacts import app, fmt, widgets


def data(state):
    return state.get("data") or {}


KEYS = widgets.Keys({
    "f": ("reclaimable only", lambda st: st.update(only_free=not st.get("only_free"))),
})


def entries(state):
    found = data(state).get("entries", [])
    if state.get("only_free"):
        return [e for e in found if e["reclaimable"] and not e.get("nested")]
    return found


def bar(fraction, width=16):
    filled = max(0, min(width, int(round(width * fraction))))
    style = "red" if fraction >= 0.85 else ("yellow" if fraction >= 0.7 else "green")
    return Text.assemble(("█" * filled, style), ("█" * (width - filled), "dim"))


def row(entry):
    return [
        Text("♻" if entry["reclaimable"] and not entry.get("nested") else " ",
             style="green" if entry["reclaimable"] else "dim"),
        Text(("  ↳ " if entry.get("nested") else "") + widgets.plain(entry["label"]),
             style="dim" if entry.get("nested") else ""),
        Text(fmt.size(entry["bytes"]), justify="right",
             style="bold" if entry["bytes"] > 5 * 1024**3 else ""),
        Text(widgets.plain(entry["path"]), style="dim"),
    ]


def volume_line(volume):
    return Text.assemble(
        (f"{widgets.plain(volume['mount'])[:24]:<24}", ""),
        bar(volume["percent"] / 100),
        (f"  {volume['percent']:.0f}% used", "dim"),
        (f"   {fmt.size(volume['free'])} free", ""),
        (f" of {fmt.size(volume['total'])}", "dim"),
    )


def render(state, console):
    payload = data(state)
    volumes = payload.get("volumes", [])
    free = sum(v["free"] for v in volumes) if volumes else 0
    reclaimable = payload.get("reclaimable", 0)
    worst = max((v["percent"] for v in volumes), default=0)

    head = widgets.header(
        Text.assemble(
            ("Disk", "bold"),
            ("   ", ""),
            (fmt.size(reclaimable), "bold green"),
            (" of what's here is cache you can delete", "dim"),
        ),
        warn=worst >= 85,
        warning=f"a volume is {worst:.0f}% full",
    )
    kpis = widgets.kpi_row([
        ("free", fmt.size(free)),
        ("reclaimable", fmt.size(reclaimable)),
        ("fullest volume", f"{worst:.0f}%"),
        ("local snapshots", str(payload.get("snapshots", 0))),
    ])
    disks = widgets.stack(*[volume_line(v) for v in volumes])
    note = Text(
        "♻ = safe to delete, the tool rebuilds it.  ↳ = already counted in the "
        "line above it.  This fetch takes ~20s, so "
        "the manifest says stale_after 6h — run it from cron.",
        style="dim italic",
    )
    keys = widgets.help_line(widgets.Cursor.KEYS, KEYS.help)
    table = widgets.scrolling_table(
        entries(state),
        state["cursor"],
        [
            widgets.Column("", width=1),
            widgets.Column("what", width=24),
            widgets.Column("size", width=10, justify="right"),
            widgets.Column("path", drop_below=100),
        ],
        row,
        height=widgets.remaining_height(console, head, kpis, disks, note, keys),
        console=console,
        title="Biggest locations",
        empty="nothing measured yet — run `tart fetch mac-space`",
    )
    return widgets.stack(head, kpis, disks, table, note, keys)


app.run(
    render=render,
    state={"cursor": widgets.Cursor(), "only_free": False},
    keys=KEYS,
    rows=entries,
    summary=lambda st: {
        "reclaimable": data(st).get("reclaimable"),
        "volumes": [{"mount": v["mount"], "percent": v["percent"]} for v in data(st).get("volumes", [])],
        "biggest": [e["label"] for e in entries(st)[:5]],
    },
)
