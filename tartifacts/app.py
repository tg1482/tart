"""`app.run()` — the event loop every artifact needs, owned once.

An artifact supplies what's genuinely its own (what state it keeps, how it
renders, which keys it handles) and nothing else. Terminal setup, mux
registration, source polling, the select/redraw loop, and the universal
keys (`q` quit, `r` refresh) live here.

Owning the loop is also what makes headless modes possible: because
`run()` knows how to produce one frame without a terminal, every artifact
gets `--once` (print a frame and exit) and `--json` (dump the summary)
for free — which is how an agent inspects an artifact it can't see.

    from tartifacts import app

    app.run(
        title="Bedrock spend",
        manifest=MANIFEST,
        sources={"snapshot": app.FileSource(DATA_PATH)},
        state={"selected": 0},
        render=lambda st, console: my_renderable(st),
        on_key=my_key_handler,          # optional
        summary=lambda st: {"models": n},  # optional; printed by --json
    )
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable

from rich.console import Console, Group
from rich.segment import Segment, Segments
from rich.text import Text

from . import input as ck_input
from . import (
    fmt,
    index,
    registry,
    manifest as manifest_mod,
    refresh as ck_refresh,
    source as ck_source,
    status as ck_status,
    terminal as ck_terminal,
    widgets,
)

POLL_TICK = 0.2
# Non-tty rich defaults to 80x25, which crops a dashboard down to a few rows.
# Headless output is for reading, not for simulating a small pane, so default
# to something roomy; --width/--height override.
HEADLESS_WIDTH = 100
HEADLESS_HEIGHT = 45


class FileSource:
    """A JSON file watched by mtime."""

    def __init__(self, path: str | Path, poll_interval: float = ck_source.FILE_POLL_INTERVAL):
        self.path = str(path)
        self.poll_interval = poll_interval

    def read_now(self) -> Any:
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def problem(self) -> str | None:
        """Why `read_now()` returns None, in words — or None when the file
        is fine. `read_now` collapses missing, corrupt and unreadable into
        one None, which renders as "no data" and gives an agent staring at
        `--json` output nothing to distinguish "never fetched" from "a torn
        write" from "a permissions accident"."""
        try:
            with open(self.path) as f:
                json.load(f)
            return None
        except FileNotFoundError:
            return f"{self.path} does not exist — never fetched?"
        except OSError as bad:
            return f"{self.path} cannot be read: {bad}"
        except json.JSONDecodeError as bad:
            return f"{self.path} is not valid JSON: {bad}"

    def start(self, q: Queue) -> threading.Event:
        return ck_source.watch_file(self.path, q, self.poll_interval)


def run(
    *,
    render: Callable[[dict, Console], Any],
    title: str | None = None,
    manifest: str | Path | None = None,
    sources: dict[str, Any] | None = None,
    state: dict | None = None,
    rows: Callable[[dict], Any] | None = None,
    on_key: Callable[[str, dict], None] | None = None,
    keys: widgets.Keys | None = None,
    summary: Callable[[dict], Any] | None = None,
    argv: list[str] | None = None,
) -> None:
    """Only `render` is required. `title` and the data source both come
    from the manifest by default, so a path or a name isn't declared in
    two places; pass them explicitly only to override.

    `rows` (a callable returning the current row list) auto-wires a
    `widgets.Cursor` found at `state["cursor"]`: cursor keys are handled
    before `on_key`, which then only sees keys the cursor didn't claim.
    """
    state = dict(state or {})
    args = sys.argv[1:] if argv is None else argv

    # `manifest` names the file; `declared` is what's in it. Render code
    # reads it as state["manifest"] — that's where declared policy lives
    # (notably `stale_after`), so an artifact can say "this data is too old
    # to trust" without hardcoding the threshold it's meant to be declaring.
    manifest = manifest or os.environ.get("TART_MANIFEST")
    declared = manifest_mod.load(Path(manifest)) if manifest else None
    state["manifest"] = declared

    title = title or (declared.title if declared else "artifact")
    if sources is None:
        # The manifest already says where the data is; making the artifact
        # repeat it is a third copy of a path nothing checks for drift.
        sources = (
            {"data": FileSource(declared.data_path)}
            if declared and declared.data_path
            else {}
        )

    pinned = _apply_state_arg(args, state)

    if "--json" in args:
        _headless_json(sources, state, render, summary, pinned)
        return
    if "--once" in args:
        _headless_once(sources, state, render, args, pinned)
        return
    if not sys.stdin.isatty():
        # Piped/redirected with no explicit mode: one frame is the only
        # sensible thing — an interactive Live loop would spin forever
        # writing escape codes nobody reads.
        _headless_once(sources, state, render, args, pinned)
        return

    _interactive(title, manifest, sources, state, render, _keys(rows, on_key, keys), pinned)


def _keys(rows, on_key, bound=None):
    """The Cursor claims its keys, then declared bindings, then the
    artifact's `on_key` gets whatever is left — the prologue every table
    artifact used to write by hand."""

    def dispatch(key: str, state: dict) -> None:
        cursor = state.get("cursor")
        if rows is not None and isinstance(cursor, widgets.Cursor):
            if cursor.handle(key, len(rows(state) or [])):
                return
        if bound is not None and bound.handle(key, state):
            return
        if on_key is not None:
            on_key(key, state)

    return dispatch


def _apply_state_arg(args: list[str], state: dict) -> set[str]:
    """`--state '{"show_detail": true}'` merges JSON into the state dict
    before rendering.

    Without it, every path reached by a keypress is unverifiable headless:
    you can render the initial frame and nothing else."""
    payloads = [a.split("=", 1)[1] for a in args if a.startswith("--state=")]
    payloads += [args[i + 1] for i, a in enumerate(args) if a == "--state" and i + 1 < len(args)]
    if len(payloads) > 1:
        print("--state given more than once; pass one JSON object", file=sys.stderr)
        sys.exit(1)
    if not payloads and not any(a == "--state" for a in args):
        return set()
    try:
        overrides = json.loads(payloads[0])
    except (IndexError, json.JSONDecodeError) as bad:
        print(f"--state needs a JSON object: {bad}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(overrides, dict):
        print("--state needs a JSON object", file=sys.stderr)
        sys.exit(1)

    # --state sets plain-data keys. Replacing a live value (a Cursor, the
    # injected Manifest, anything JSON couldn't have produced) doesn't fail
    # loudly — it kills the keyboard for that frame or raises from deep in a
    # widget. So refuse to overwrite any key currently holding a live value.
    live = [key for key in overrides if _is_live(state.get(key))]
    if live:
        print(
            f"--state cannot replace live values: {', '.join(sorted(live))}\n"
            "  they hold behaviour, not data — set the plain keys your render "
            "reads instead.",
            file=sys.stderr,
        )
        sys.exit(1)
    state.update(overrides)
    return set(overrides)


_JSON_SCALARS = (str, int, float, bool, type(None))


def _is_live(value: Any) -> bool:
    """True for anything JSON could NOT have produced. Complete by
    construction: a scalar or a container of scalars is data; everything
    else — a widget, the Manifest, a datetime, a set — is live. The old
    check enumerated widget/dict/list and so missed sets, dict keys, and the
    `manifest` object, each of which `--state` could then silently clobber."""
    if isinstance(value, _JSON_SCALARS):
        return False
    if isinstance(value, dict):
        return any(_is_live(k) or _is_live(v) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_is_live(v) for v in value)
    return True


def _str_arg(args: list[str], flag: str) -> str | None:
    """`--svg out.svg` or `--svg=out.svg`; None when absent."""
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith(f"{flag}="):
            return arg.split("=", 1)[1]
    return None


def _int_arg(args: list[str], flag: str, default: int) -> int:
    if flag in args:
        try:
            return int(args[args.index(flag) + 1])
        except (IndexError, ValueError):
            pass
    return default


def _load_all(sources: dict, state: dict, pinned: set[str] = frozenset()) -> None:
    """`pinned` names keys `--state` set explicitly; loading a source over
    one silently discarded it, so `--state '{"data": ...}'` — the way to
    render an artifact against sample data — did nothing at all."""
    """Synchronous read of every source — headless modes can't wait on the
    poll threads, they need data now and then exit."""
    for name, src in sources.items():
        if name not in pinned:
            state[name] = src.read_now()


def _diagnose_sources(sources: dict, state: dict, pinned: set) -> bool:
    """Report every source that failed to load, on stderr, with the fetch
    record when there is one. Returns True if anything was wrong — headless
    modes exit non-zero on it, because "no data" printing with exit 0 is
    indistinguishable (to cron, CI, an agent) from a genuinely empty
    artifact."""
    broken = False
    for name, src in sources.items():
        if name in pinned or state.get(name) is not None:
            continue
        why = getattr(src, "problem", lambda: None)()
        if why:
            broken = True
            print(f"source '{name}': {why}", file=sys.stderr)
    declared = state.get("manifest")
    if broken and declared is not None:
        last = ck_status.last_fetch(declared.path)
        if last is not None and not last.get("ok"):
            print(
                f"last fetch: FAILED ({ck_status.describe(last)}, "
                f"{fmt.age(time.time() - last.get('at', 0))} ago) — "
                f"tart logs {declared.path.stem}",
                file=sys.stderr,
            )
        elif declared.fetch:
            print(f"try: tart fetch {declared.path.stem}", file=sys.stderr)
    return broken


def _headless_json(sources: dict, state: dict, render, summary, pinned=frozenset()) -> None:
    _load_all(sources, state, pinned)

    # Exercise render before printing numbers, so `--json` means "the
    # artifact works AND here is its summary". It used to run summary() only,
    # while the docs promised it "executes the artifact" — so an agent gating
    # on it shipped a dashboard that was a traceback on screen. render_lines
    # forces full evaluation, catching a __rich_console__ that raises during
    # drawing, not only a render() that raises at call time.
    console = Console(width=HEADLESS_WIDTH, height=HEADLESS_HEIGHT, force_terminal=True)
    console.render_lines(render(state, console), console.options, pad=False)

    if summary is None:
        # No summary declared — the raw source data is still more useful
        # than nothing, and keeps --json meaningful for every artifact.
        payload = {name: state.get(name) for name in sources}
    else:
        payload = summary(state)
    try:
        print(json.dumps(payload, indent=2, default=str, allow_nan=False))
    except ValueError as bad:
        # NaN/Infinity are not JSON. Emitting them gives agents something
        # that silently becomes null in jq; better to fail loudly.
        print(f"summary is not representable as JSON: {bad}", file=sys.stderr)
        sys.exit(1)
    # After the payload: partial numbers still flow to whoever pipes them,
    # but the exit code says the artifact is not actually healthy.
    if _diagnose_sources(sources, state, pinned):
        sys.exit(1)


def _headless_once(sources: dict, state: dict, render, args: list[str], pinned=frozenset()) -> None:
    _load_all(sources, state, pinned)
    svg = _str_arg(args, "--svg")
    console = Console(
        width=_int_arg(args, "--width", HEADLESS_WIDTH),
        height=_int_arg(args, "--height", HEADLESS_HEIGHT),
        force_terminal=True,
        record=svg is not None,
    )
    # Crop to the requested height, because that is what the interactive
    # loop does: rich's screen=True Live shows the top of an over-tall
    # frame and drops the rest. Printing the whole thing here made the one
    # failure mode the docs warn loudest about the one you could not see.
    lines = console.render_lines(render(state, console), console.options, pad=False)
    console.print(Segments(
        [segment for line in lines[: console.size.height] for segment in (*line, Segment("\n"))]
    ))
    if svg is not None:
        # An artifact nobody can see is hard to document; this exports the
        # frame as it really renders, colours and all, for a README.
        Path(svg).parent.mkdir(parents=True, exist_ok=True)
        Path(svg).write_text(console.export_svg(title=state.get("_svg_title", "tart")))
    if _diagnose_sources(sources, state, pinned):
        sys.exit(1)


def _interactive(title, manifest, sources, state, render, keys, pinned=frozenset()) -> None:
    from rich.live import Live  # local: only needed in the interactive path

    console = Console()
    updates: Queue = Queue()
    triggers = []
    for name, src in sources.items():
        if name not in pinned:
            state[name] = src.read_now()  # paint real data on the first frame, not an empty one
        triggers.append(src.start(updates))

    manifest_path = str(manifest) if manifest else None
    if manifest_path:
        registry.register(manifest_path, title, pane=registry.current_pane())
        index.remember(Path(manifest_path))  # launched once => findable forever

    # Keeps `data` fresh from the manifest's own `fetch`/`stale_after`, so
    # a long-open artifact doesn't sit stale waiting on an external cron.
    keeper = ck_refresh.Keeper(state["manifest"]) if state.get("manifest") else None
    if keeper:
        keeper.start()

    reader = ck_input.Reader()
    source_names = list(sources)
    data_error: str | None = None
    name = Path(manifest_path).stem if manifest_path else None

    def frame():
        # The warning bar rides ABOVE the artifact's own frame: rich's
        # screen=True Live crops at the bottom, so that's the one edge a
        # broken artifact can't push its own bad news off of. One row
        # sacrificed, but only while something is actually wrong.
        rendered = render(state, console)
        warning = _warning(data_error, keeper, name)
        if warning is None:
            return rendered
        return Group(Text(warning, style="bold black on yellow", no_wrap=True,
                          overflow="ellipsis"), rendered)

    try:
        with ck_terminal.raw_mode(), Live(
            frame(), console=console, screen=True, refresh_per_second=4
        ) as live:
            while True:
                event = reader.poll(POLL_TICK)
                if isinstance(event, ck_input.Key):
                    if ck_input.is_quit(event):
                        return
                    if event.value == "r":
                        # Refetch when the manifest says how; re-reading a
                        # stale file would otherwise make `r` a no-op.
                        if keeper:
                            keeper.force()
                        for trigger in triggers:
                            trigger.set()
                    elif keys is not None:
                        keys(event.value, state)

                try:
                    while True:
                        evt = updates.get_nowait()
                        if evt.ok:
                            data_error = None
                            # Single-source artifacts are the common case; with
                            # several, events don't carry which one fired, so
                            # re-read each. Cheap (local file/cached cmd output).
                            if len(source_names) == 1:
                                if source_names[0] not in pinned:
                                    state[source_names[0]] = evt.value
                            else:
                                _load_all(sources, state, pinned)
                        else:
                            # The watcher went to the trouble of saying WHY the
                            # file didn't parse; dropping that on the floor left
                            # the artifact rendering old data with no indication.
                            data_error = str(evt.value)
                except Empty:
                    pass

                # Redraw every tick, not only on new data: a keypress moves
                # the cursor without changing any source.
                live.update(frame())
    finally:
        if manifest_path:
            registry.unregister()


def _warning(data_error: str | None, keeper, name: str | None) -> str | None:
    """The one line most worth interrupting the artifact for. A broken data
    file beats a failed fetch: the first means what's on screen is not what's
    on disk, the second only that it won't get fresher."""
    if data_error:
        return f"⚠ data file unreadable: {data_error}"
    last = keeper.last_fetch if keeper else None
    if last is not None and not last.get("ok"):
        hint = f" — tart logs {name}" if name else ""
        return (
            f"⚠ fetch failed ({ck_status.describe(last)}, "
            f"{fmt.age(time.time() - last.get('at', 0))} ago){hint}"
        )
    return None

