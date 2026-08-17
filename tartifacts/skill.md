# tart — agent reference

A **live terminal artifact** is a dashboard that stays open, keeps its own
data fresh — or tells you it isn't — and stays findable afterwards. Three pieces:

- a `.tart/<name>.json` **manifest** — what it's called, what produces its
  data, where that lands, when it goes stale
- a **fetch** script — writes JSON to `tartifacts.data_path()`
- a **render** script — a `render(state, console)` returning any `rich`
  renderable

tart owns everything between, which is what makes it *live*: terminal
setup, the event loop, polling, refresh from declared staleness,
discovery, and headless output. The manifest is the only
place a path is declared, so neither script repeats it.

## CLI

| command | does |
|---|---|
| `tart list` | every declared artifact, its repo, and where it's live (pane, tty, or pid) |
| `tart run <name>` | launch it interactively (fetches first if data is stale/missing) |
| `tart render <name>` | print ONE frame and exit — no terminal needed (`--width`/`--height` to size it) |
| `tart render <name> --json` | print its `summary()` as JSON |
| `tart render <name> --state '<json>'` | merge JSON into state first — how you review a keypress path headlessly |
| `tart render <name> --states` | render the base frame plus every state declared in the manifest — the smoke matrix |
| `tart fetch <name>` | re-run its data-producing command now |
| `tart logs <name>` | the last fetch's outcome and output — including background and cron fetches |
| `tart cron <name>` | register a standing fetch in the managed crontab block, current PATH and absolute binary baked in (`--show` to preview without installing) |
| `tart cron --sync` | refresh the managed block: every trusted `auto_refresh` artifact (stale_after ≥ 10m) plus every name ever `tart cron <name>`-registered; staggered minutes, unmanaged lines untouched |
| `tart restart <name> \| --all` | re-exec live artifacts in place (SIGUSR1) — same pane, new code; run it after upgrading tart |
| `tart register <path>` | adopt a manifest living anywhere — records its location and trusts it |
| `tart trust <name>` | agree to run this manifest's commands (`--all`, `--list`, `--forget <name>`) |
| `tart roots add <path>` | register a workspace to scan (`rm` to remove, bare `roots` to list) |
| `tart reindex` | deep-search roots for manifests too deep for the scan |
| `tart --skill` | this document |

`<name>` is the manifest's filename. If two repos declare the same name,
`run` refuses to guess and prints both `<repo>/<name>` forms — use those.
A direct path to a `.json` also works.

`--state '{"show_detail": true}'` reaches the frame a keypress would
produce, for the plain-data keys your render reads. It cannot set live
widgets — a `Cursor` holds behaviour, not data, so
`--state '{"cursor": {"index": 25}}'` is refused rather than silently
replacing it. Keep what you want to review headlessly in plain state.

`--height` crops, exactly as the interactive loop does — so an
over-tall frame looks headless the way it will look on screen.

**Checking an artifact works** needs no separate command: `tart render
<name> --json` renders it (a crashing `render` or an unreadable data file
is a non-zero exit, with the why on stderr) and prints the summary, `tart
fetch <name>` reports precisely if the fetch fails or writes the wrong
path, `tart list` flags stale data, failed fetches, and manifests that
won't load, and `tart logs <name>` shows what the last fetch actually said.

**Every fetch is recorded** — CLI, `tart run`'s pre-launch refresh, the
background keeper, a cron line — under `<TART_HOME>/artifacts/`: the last
outcome (exit code, duration, output tail) plus an appended `fetch.log`.
That record is what `tart list`'s `✗ fetch failed (...)`, the artifact's
own warning bar, and `tart logs` read. A fetch that fails overnight in
cron is therefore visible the next morning in all three, with its stderr
intact. The record also carries the last *success*, so a persistent
failure reads as `✗ fetch failing for 2d (exit 1)` — how long it's been
broken, not how recent the newest attempt was.

**Freshness has two layers.** The keeper (in-process, `auto_refresh`)
refetches while an artifact is open — good for fast cadences and the `r`
key, but it only exists while a pane is open. `tart cron --sync` is the
standing order: a managed crontab block fetching every trusted
`auto_refresh` artifact at its `stale_after` cadence whether anything is
open or not. Sub-10-minute cadences stay keeper-only.

**Exit codes**, so cron and CI can gate on tart rather than grep its output:
`render` (both modes) is non-zero if the artifact fails to render OR a
declared data file is missing/unparseable — the frame or summary still
prints, stderr says which file and why, so partial numbers flow but the
exit says unhealthy. **Stale data is a warning, not a failure**: render
exits 0 but stderr says how old the data is against its declared
`stale_after` — an agent reading `--json` must check stderr or it will
read hours-old numbers without knowing; `fetch` passes through the fetch command's status
(124 timeout, 128+N if signal-killed, 1 if it wrote nowhere); `list` is 1
if any manifest is unreadable; an unknown or ambiguous name is 1.

## Where a manifest can live

Two ways tart finds one, and they differ in whether you have to say yes:

| how | where | trust |
|---|---|---|
| **scanned** | `.tart/<name>.json` in the cwd, `~/.tart/`, or a configured root | needs `tart trust <name>` |
| **registered** | anywhere at all — `tart register <path>` | trusted on registration |

`register` records the LOCATION; it never copies the file. A manifest names
paths inside its own repo, so a copy would rot the moment that repo moved —
the same reason `systemctl enable` symlinks rather than copies.

Registering trusts it because naming a file on the command line *is* the
choice the gate exists to capture. Editing it still asks again: trust is
keyed by content hash, not path.

## Trust

`run` and `fetch` are shell commands, and tart finds manifests by scanning
your roots at any depth — so a cloned repo can put one where `tart run`
will reach it, and `tart render` executes too despite reading like a read.

A manifest does nothing until you `tart trust <name>`. The refusal prints
the commands it would run *and the script files those commands name*,
because the manifest usually just says `python dash.py` — the code is in
the script, and that is what you should read.

Trust is keyed to the manifest's **content**, so editing a trusted manifest
asks again. It does **not** cover the scripts: those can change afterwards
without asking, exactly as direnv covers `.envrc` and not what it sources.
The gate stops a manifest you never opened being executed because a scan
found it; it is not a guarantee about code you already agreed to run.

`tart trust --all` trusts everything currently declared: the one-time
migration for a machine that predates this.

## Environment

| variable | meaning |
|---|---|
| `TART_HOME` | where tart keeps roots, index and live entries (default `~/.tart`) |
| `TART_MANIFEST` | set by tart for the artifact and fetch scripts it launches — read it via `tartifacts.data_path()`, don't set it by hand |
| `TART_PYTHON` | set by tart for every command it spawns: its own interpreter, which has `rich`+`tartifacts` — use it as the manifest's interpreter unless you need extra deps |
| `TART_CRONTAB` | a file standing in for the real crontab — `tart cron` reads/writes it instead of running `crontab`. The seam tests use so cron registration is testable without touching a real machine |
| `TMUX_PANE` / `HERDR_PANE_ID` | read if present, to say *where* an artifact is running |

`TART_HOME` is the seam for an isolated config — a scratch workspace, a
second set of roots, or a test run that must not touch your real state.
It is deliberately not XDG-aware: honouring `XDG_STATE_HOME` would
relocate an existing `~/.tart` out from under its owner, silently
unregistering every root.

**As an agent, use `tart render <name> --json` to read an artifact's
numbers.** It renders the artifact (surfacing a crashing `render` as a
non-zero exit) and then prints the summary, so a clean exit means the
dashboard works *and* here are its numbers. Never screen-scrape the TUI.
and `tart list` names any manifest that fails to load.

## Manifest — `.tart/<name>.json`

```json
{
  "title": "Bedrock spend",
  "run": "$TART_PYTHON bin/dash.py",
  "data": "bin/data/bedrock_usage.json",
  "fetch": "$TART_PYTHON bin/snapshot.py",
  "stale_after": "4h",
  "auto_refresh": true
}
```

| field | required | meaning |
|---|---|---|
| `title` | yes | shown in the UI and in `tart list` |
| `run` | yes | command that launches the artifact |
| `data` | no | JSON file the artifact reads, relative to the repo root |
| `fetch` | no | command that produces `data` |
| `env_file` | no | KEY=VALUE file loaded into `run`/`fetch`'s environment (`~` expands) — where secrets live |
| `stale_after` | no | `30s` / `45m` / `4h` / `2d` — when `data` stops being trustworthy |
| `auto_refresh` | no | keep the data fresh: refetch while open (the keeper), and `tart cron --sync` installs a standing cron line for it |
| `states` | no | `--state` payloads worth checking, e.g. `[{"detail": true}, {"scale": "7d"}]` — `tart render <name> --states` renders each |

Paths are relative to the **repo root** (one level above `.tart/`), and
`run`/`fetch` execute from there, so they work from any cwd.

**`env_file`** exists because the same fetch runs in three environments —
your shell, the background keeper, and cron — and only your shell has your
exports. It's systemd's `EnvironmentFile=`: KEY=VALUE lines (`#` comments
and an `export ` prefix tolerated, matching quotes stripped, no
interpolation), loaded by tart before spawning, overriding the inherited
environment so all three run identically. Keep secrets there rather than
in the manifest (which gets committed) or the command string (which shows
in `ps`). A declared `env_file` that can't be loaded fails the command
loudly and is recorded like any fetch failure. Trust hashes the manifest,
so *pointing* at a different file re-asks; the file's contents are data
and deliberately outside the hash — as with direnv and what `.envrc`
sources.

**`$TART_PYTHON` is the default interpreter for `run` and `fetch`.** tart
sets it to its own interpreter for every command it spawns, and that
interpreter has `rich` and `tartifacts` by construction (they are tart's
own dependencies). One process, ~20 MB, no resolver in the loop.

The alternative, `uv run --with rich --with tartifacts python bin/dash.py`,
is for artifacts that need **extra** libraries (`--with pandas ...`) — but
know the cost: `uv run` stays resident as a supervisor for the life of the
artifact (~24-34 MB doing nothing) on top of the Python it spawned, ~60 MB
per artifact instead of ~20, plus resolver latency on every single fetch.
Reach for it when you need the isolated env, not by default.

Declaring `data`/`fetch`/`stale_after` is what makes an artifact
self-contained rather than silently depending on someone's crontab.
Without them `tart run` can't self-heal stale data, and `tart list`
can't tell you the data is stale.

## Writing an artifact

Two scripts and a manifest. The manifest declares the data path **once**;
neither script repeats it.

**Fetch script** — produces the data:

```python
import tartifacts

tartifacts.write_data({                       # to the manifest's "data"
    "rows": [{"name": "a", "count": 1}, {"name": "b", "count": 4}],
    "daily": [1, 3, 2, 5, 4, 6, 4],
})
```

Atomic matters: a live artifact polls that file by mtime, so a plain
`open(path, "w")` is visible half-written and reads as "no data yet".
`tartifacts.data_path()` still gives you the path if you need to write it
yourself.

Run it with `tart fetch <name>` (which sets `TART_MANIFEST`), not bare.

**Artifact script** — renders it:

```python
from tartifacts import app, widgets
from rich.console import Group
from rich.panel import Panel

def rows(state):
    # `state["data"]` is None (not {}) when the file is missing or
    # unparseable, so `.get("data", {})` will NOT save you here.
    return (state.get("data") or {}).get("rows", [])


COLUMNS = [
    widgets.Column("Name", width=24),
    widgets.Column("Count", width=8, justify="right"),
    widgets.Column("Note"),          # width=None flexes to fill
]


def row_fn(item):
    """One item -> one cell per column. Strings or rich renderables."""
    return [item["name"], str(item["count"]), item.get("note", "—")]


def render(state, console) -> Group:
    snapshot = state.get("data")               # from the manifest's "data"
    if snapshot is None:
        return widgets.stack(widgets.header("no data — run `tart fetch thing`", warn=True))
    head = widgets.header(f"Thing · {len(snapshot['rows'])} rows")
    strip = widgets.row(
        Panel(widgets.trend(snapshot["daily"], summary="7d", style="green"), title="Volume"),
        widgets.kpi_row([("Total", str(len(snapshot["rows"])))]),
    )
    foot = widgets.help_line(widgets.Cursor.KEYS, "d detail")
    return widgets.stack(
        head, strip,
        widgets.scrolling_table(
            snapshot["rows"], state["cursor"], COLUMNS, row_fn,
            height=widgets.remaining_height(console, head, strip, foot),
            console=console, title="Rows",
        ),
        foot,
    )

def on_key(key, state):                         # only keys the cursor declined
    if key == "d":
        state["show_detail"] = not state.get("show_detail", False)

def summary(state):                             # --json output; provide it
    return {"rows": len(rows(state))}

app.run(
    render=render,
    state={"cursor": widgets.Cursor()},
    rows=rows,                                      # auto-wires the cursor
    on_key=on_key, summary=summary,
)
```

**Only `render` is required.** `title` and the data source both come from
the manifest; `rows` auto-handles cursor keys before `on_key` sees them.

`app.run(...)` params: `render` (required), then `title`, `manifest`,
`sources`, `state`, `rows`, `on_key`, `summary`, `argv` — each an
override for something otherwise derived. You get `--once`/`--json`,
registration, and the refresh keeper for free.

**Sources** — omit entirely and you get `state["data"]` from the
manifest's `data` file, redrawn the moment its mtime changes. Pass
`sources={"name": app.FileSource(path)}` only to watch something else.

`state["manifest"]` is a `Manifest` object, not a dict. Useful members:
`.data_age()` (seconds since the data was written), `.is_stale()`
(True/False, or None when it can't be judged), `.stale_after`, `.data_path`,
`.root`. Use `.is_stale()` for a staleness header rather than comparing
timestamps yourself.

**Reserved state keys**: tart owns `manifest` and every name in `sources`
(`data` by default). Using one for your own value breaks whatever owns it.

## Widgets — `tartifacts.widgets`

**We're rich (or at least, we use Rich).** `render()` may return any rich
renderable: `Table`, `Panel`, `Layout`, `Tree`, `Progress`, `Syntax`,
`Columns`, or your own `__rich_console__`. tart is not a rendering framework
and there is no vocabulary here you are required to learn. In `examples/`,
`mac-vitals` makes more raw rich calls than widget ones and `claude-tools`
builds its whole bar chart with `Text.assemble`.

These exist only for what rich has no answer for, which is the stuff that
only shows up once a dashboard is live and resizable: scrolling a long list
with a selected row, sparklines, working out how many rows are left for a
table, stripping control characters out of data you scraped, and key
bindings that cannot drift from the footer. Take one, take none, mix
freely.

| widget | what |
|---|---|
| `Cursor()` | selection + scroll — keep it in `state`; `cursor.selected(items)` gives the current item (or `None`), clamped, for a detail panel |
| `scrolling_table(items, cursor, columns, row, height, console, ...)` | windows items through the cursor; `height` is total space, border and header included |
| `Column(header, width=, justify=, drop_below=)` | `width` is a *minimum*, not exact — the table expands to fill; `drop_below=N` drops the column under N cols |
| `kpi_row(tiles, title=)` | compact label/value strip, 2 lines (4 with a title) |
| `row(*panels, min_width=30, drop_below=None)` | panels side by side; wraps, then stacks, or hides under `drop_below` |
| `trend(values, summary=, label=, style=)` | one-line series that fills its width, keeping the recent end |
| `tartifacts.write_data(obj)` | write your data atomically — a live artifact polls the file and a plain `open()` is visible half-written |
| `spark_text(values, style=)` | just the block characters, no width logic |
| `timeline(cells, style_for, ...)` | thin one-cell-per-bucket strip; `None` cell = nothing happened |
| `header(text, warn=, warning=)` | status line, yellow when warning |
| `Keys({key: (label, action)})` | bindings that carry their own help — pass to `app.run(keys=)`, print with `KEYS.help` |
| `help_line(*parts)` | footer; appends `r refresh` / `q quit` (de-duplicated, so don't pass them) |
| `stack(*renderables)` | vertical stack, skips `None` |
| `remaining_height(console, *fixed)` | rows left for a scrolling region; pass the renderables |
| `height_of(console, renderable)` | rows one renderable occupies at this width |

### Keys — bind once, never let the footer lie

Declare bindings with `Keys` rather than an `if/elif` in `on_key`. One
definition drives dispatch *and* the help line, so renaming a key can't
leave the footer advertising the old one.

```python
KEYS = widgets.Keys({
    "t": ("timescale", lambda st: st.update(scale=NEXT[st["scale"]])),
    "d": ("details", lambda st: st.update(detail=not st["detail"])),
})

def render(state, console):
    ...
    keys = widgets.help_line(widgets.Cursor.KEYS, KEYS.help)

app.run(render=render, state={"scale": "24h", "detail": False}, keys=KEYS)
```

Each action takes `state` and mutates it; return value ignored. `on_key` is
still there for anything dynamic, and runs after `Keys` and the cursor.

**Use these letters for these meanings.** An agent reading someone else's
artifact should be able to guess the keys, so prefer a conventional letter
over a memorable one:

| key | means |
|---|---|
| `t` | cycle **timescale** (24h -> 7d -> 30d) |
| `d` | toggle **detail** panel |
| `f` | cycle **filter** |
| `s` | cycle **sort** |
| `a` | toggle showing **all** / hidden rows |
| `e` | toggle **errors only** |

Reserved, do not rebind: `q` quit, `r` refresh, `j`/`k` and arrows, `g`/`G`,
`ctrl-d`/`ctrl-u` (the cursor owns those).

Any state a key toggles should be a plain value in `state`, because
`--state '{"detail": true}'` is then how an agent renders that view
headlessly — a key nobody can press from a pipe is a view nobody can check.
Then declare those payloads in the manifest's `states` list: `tart render
<name> --states` renders every one, so the view behind a keypress gets
checked by the same command that checks the base frame. The crash always
lives in the one view the smoke test skipped.

### Formatting — `tartifacts.fmt`

Pure helpers, so artifacts stop each rewriting them:
`fmt.usd(1234.5)` → `$1,234.50` · `fmt.grouped(5911)` → `5,911` ·
`fmt.size(1536)` → `1.5 KB`.

### Responsive layout

A terminal has no scrollbar — `screen=True` crops. So narrow and short are
both real, and both are handled by rendering against the width you're
actually given rather than the one you hoped for:

- **Time series trim to the recent end.** Rich crops and wraps at the
  right, so a 30-day series in a narrow panel would hide *today*.
  `trend` and `timeline` drop the oldest buckets instead and rescale to
  what's left. Never `f"{spark}"` into an f-string — that's a fixed-width
  string again, and it wraps.
- **`trend` sheds its caption before its data**, `summary` first then
  `label`, so a squeezed panel shows a shorter series, not three
  characters of one.
- **`row()` reflows**: three panels side by side at 120 columns, 2+1 at
  80, stacked at 50. No width checks at the call site.
- **Stacking costs height, so dense detail should hide instead.**
  `row(..., drop_below=80)` disappears under 80 columns rather than
  stacking and shoving the table off the bottom. Use it on the row you'd
  sacrifice first — a 4-column table ellipsised to `dup…` is not carrying
  its rows.
- **Budget height by measuring, not counting.** `remaining_height(console,
  header, strip, detail, footer)` measures each; a hand-counted
  `reserved=1+6+12` is silently wrong the moment a row wraps or a header
  line grows, and the symptom is the bottom of your table cropped off.
  `None` costs nothing, so optional panels pass straight through. Pass
  ints only for something you can't render yet.


## Discovery

Two separate questions, and `tart list` crosses them:

- **declared** — two sources, unioned. A *scan* of `./.tart/`, `~/.tart/`,
  and each configured root's own and immediate children's `.tart/`
  (`tart roots add <path>` registers a workspace). Plus an *index* of
  every manifest tart has resolved before, which is how artifacts deeper
  than the scan — inside a git worktree, say — stay findable after being
  used once, by name or by path. Index entries are re-validated on read
  and pruned when the file is gone, so a moved or deleted repo drops out
  rather than lingering.

**How the index stays current.** An artifact is remembered whenever tart
resolves it — `run`, `render`, `fetch`, by name or by path — and
forgotten when its file disappears. If a lookup is *about* to fail, tart
deep-searches the roots once and retries before giving up, which is what
re-finds a manifest that moved. `tart reindex` does that search on demand.
The deep search costs ~1s against ~30ms for the normal path, so it only
ever runs on an explicit reindex or a would-be failure.
- **live** — every artifact whose *process is actually running*, recorded
  in `~/.tart/live/`.

Liveness is checked, not asserted: each entry carries a pid, and a reader
confirms the process exists before reporting it, deleting entries that
don't. A `kill -9` therefore self-heals instead of leaving a phantom.

This needs no multiplexer — it works in a plain shell, over ssh, in
screen, zellij, or a systemd unit. When you *are* inside tmux or herdr the
pane id is recorded too, because `wA:pG` beats a tty as an answer to
"where", but nothing depends on it.

## Recipes

**Add an artifact to a repo**

Copy the two examples above — they are executed in CI, so they run as
printed.

1. `mkdir -p .tart` and write the manifest — it declares the data path.
2. Fetch script: write to `tartifacts.data_path()`. Run it with
   `$TART_PYTHON`, which has tart importable already.
3. Artifact script: a `render(state, console)` reading `state["data"]`.
4. `tart fetch <name>` then `tart render <name>` — no pane needed.
5. `tart trust <name>` — a manifest runs nothing until you do.
6. `tart roots add <workspace>` once, if it isn't registered.

**Debug one that's not showing data**
`tart render <name> --json` runs it and surfaces a crash or an unreadable
data file (non-zero exit, why on stderr); `tart logs <name>` shows the last
fetch's output — the place a background or cron failure's stderr survives;
`tart fetch <name>` reruns the producer and reports a wrong write path;
`tart list` flags stale data, failed fetches, and unreadable manifests.

**Read an artifact's data without a terminal**
`tart render <name> --json`. Falls back to raw source data if the artifact
declares no `summary()`.

## Gotchas

- **Developing tart itself? `--with-editable <checkout>`, not
  `--with <path>`.** `uv run --with <path>` builds and caches a wheel, so
  edits silently don't apply. Artifacts that just *use* tart should depend
  on the released package: `--with tartifacts`.
- **Editing an artifact's own run script restarts it automatically.** The
  live loop watches the `.py`/`.sh` files named in `run` and re-execs when
  one changes — data hot-reloads were leaving old code running against new
  data until it crashed. Two limits: modules the script *imports* aren't
  watched, and **tart itself isn't either** — after upgrading tart, `q`
  and `tart run <name>` again.
- **rich's `screen=True` Live crops, it doesn't paginate.** Anything below
  an over-tall table silently disappears — budget with
  `remaining_height(console, *your_fixed_renderables)`, which measures them
  rather than trusting a count you'd have to keep in sync.
- **Side-by-side panels: use `widgets.row`, not rich's `Columns`.**
  `Columns` sizes to content and leaves a gap instead of stretching
  panels; `row(...)` stretches each to an equal share (and wraps, then
  stacks, on narrow terminals). This has been rediscovered and
  hand-rolled per artifact — the widget already exists.
- **Never interpolate a series into an f-string.** `f"[green]{spark}[/] {caption}"`
  is a fixed-width string: rich wraps or ellipsizes it at the right, hiding
  the newest buckets. Pass `widgets.trend(values, summary=caption)` and let
  it size itself.
- **A flexible `Column` needs the fixed ones to fit**; `scrolling_table`
  bounds it for you, but declaring more fixed width than the terminal has
  will still squeeze things.
- **A fetch script needs tart importable**, since it calls
  `tartifacts.data_path()` — `$TART_PYTHON` has it; a bare `python3` or a
  `uv run` without `--with tartifacts` does not.
