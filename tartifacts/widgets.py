"""Composable pieces for tart render functions — not a widget framework.

Everything here is either a plain function returning a rich renderable, or
(for the one thing that genuinely has state) a small object you own and
keep in your own state dict. Nothing registers itself, nothing owns your
layout, and you can use one of these and hand-roll the rest.

Keys compose by chain of responsibility. tart's loop takes `q`/`r`
first, then calls your `on_key`; inside it you offer the key to stateful
widgets and fall through to your own:

    def on_key(key, state):
        if state["cursor"].handle(key, total=len(rows(state))):
            return                       # consumed j/k/g/G/ctrl-d/ctrl-u
        if key == "d":
            state["show_detail"] = not state["show_detail"]

A widget's `handle()` returns True when it consumed the key, so your own
bindings can never be silently shadowed — whatever a widget doesn't claim
reaches you.

Dashboards here are read-only: keys navigate and toggle, they never edit.
There is no text entry and so no modal state — a key means one thing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from math import isfinite

from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.measure import Measurement
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import input as ck_input

MIN_VISIBLE_ROWS = 3
TABLE_CHROME = 4       # panel border (2) + column header and its rule (2)
SPARK_CHARS = "▁▂▃▄▅▆▇█"
MIN_SPARK = 8          # below this a series says nothing; drop labels instead
MIN_PANEL_WIDTH = 30   # narrower than this and a panel is unreadable
# C0 controls and DEL, minus tab/newline which rich handles itself.
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class Cursor:
    """A selected row plus its scroll offset — the one stateful widget.

    Keep it in your state dict (`state={"cursor": widgets.Cursor()}`) and
    it survives across frames. `handle()` moves it; `window()` slices your
    rows to what fits and records the viewport height so paging keys know
    how far to jump.
    """

    KEYS = "↑/↓ j/k move · g/G top/bottom · ctrl-d/u page"

    def __init__(self) -> None:
        self.index = 0
        self.offset = 0
        self._viewport = 0        # unmeasured until window() renders once

    def handle(self, key: str, total: int) -> bool:
        """True if this key moved the cursor, False to let it fall through
        to the dashboard's own bindings."""
        if total <= 0:
            return key in ("j", "k", "g", "G", "\x04", "\x15", ck_input.UP, ck_input.DOWN)
        last = total - 1
        if key in ("j", ck_input.DOWN):
            self.index = min(self.index + 1, last)
        elif key in ("k", ck_input.UP):
            self.index = max(self.index - 1, 0)
        elif key == "g":
            self.index = 0
        elif key == "G":
            self.index = last
        elif key == "\x04":  # ctrl-d
            self.index = min(self.index + self._page(), last)
        elif key == "\x15":  # ctrl-u
            self.index = max(self.index - self._page(), 0)
        else:
            return False
        return True

    def window(self, rows: Sequence[Any], height: int) -> tuple[list[Any], int]:
        """(visible rows, index of the first one). Clamps the selection to
        the row count and scrolls just enough to keep it on screen."""
        height = max(1, height)
        self._viewport = height
        if not rows:
            self.index = self.offset = 0
            return [], 0
        self.index = max(0, min(self.index, len(rows) - 1))
        if self.index < self.offset:
            self.offset = self.index
        elif self.index >= self.offset + height:
            self.offset = self.index - height + 1
        self.offset = max(0, min(self.offset, max(0, len(rows) - height)))
        return list(rows[self.offset : self.offset + height]), self.offset

    def selected(self, items: Sequence[Any]) -> Any | None:
        """The item under the cursor, or None when the list is empty.

        Clamps a stale index first, so reading the selection BEFORE the
        table renders — the order a detail panel needs, and the one the docs
        recommend — can't IndexError when the list shrank since the last
        frame. `items[cursor.index]` alone does, and it took down live
        dashboards on the fetch that dropped a row."""
        if not items:
            return None
        self.index = max(0, min(self.index, len(items) - 1))
        return items[self.index]

    def _page(self) -> int:
        """How far ctrl-d/u jump. Falls back before the first render, when
        no height has been measured yet."""
        return self._viewport or MIN_VISIBLE_ROWS

    def range_label(self, total: int) -> str:
        """'12-25 of 27', or '' when everything fits — for a panel title.

        Empty until `window()` has measured a height: a title built BEFORE
        the table renders (the natural call order) otherwise reported a
        guessed viewport, e.g. "1-3 of 8" above a table showing all 8."""
        if not self._viewport or total <= self._viewport:
            return ""
        return f"{self.offset + 1}-{min(total, self.offset + self._viewport)} of {total}"


class Keys:
    """Key bindings that carry their own help text.

    The same contract `Cursor` has: one definition drives both dispatch and
    the footer, so a renamed key can't leave the help line lying. Written
    out by hand it was an if/elif in `on_key` plus a matching string in
    `help_line`, and nothing kept the two honest.

        KEYS = widgets.Keys({
            "t": ("timescale", lambda st: st.update(scale=next(st["scale"]))),
            "d": ("details", lambda st: st.update(detail=not st["detail"])),
        })

        app.run(..., keys=KEYS)
        ...
        widgets.help_line(widgets.Cursor.KEYS, KEYS.help)
    """

    def __init__(self, bindings: dict[str, tuple[str, Callable[[dict], Any]]]):
        self.bindings = dict(bindings)

    @property
    def help(self) -> str:
        return " · ".join(f"{key} {label}" for key, (label, _) in self.bindings.items())

    def handle(self, key: str, state: dict) -> bool:
        """True if this key was bound, so the caller stops looking."""
        found = self.bindings.get(key)
        if found is None:
            return False
        found[1](state)
        return True


@dataclass
class Column:
    header: str
    width: int | None = None          # fixed width; None flexes to fill
    justify: str = "left"
    no_wrap: bool = True
    drop_below: int | None = None     # drop this column under N console cols


def plain(value: RenderableType) -> RenderableType:
    """Strip terminal control characters out of a data-derived string.

    A fetch script scrapes a git branch, a CI status, a log line — all
    routinely coloured — and those bytes went straight to the terminal, so
    data could clear the screen or set the window title on every frame. A
    `Text` is left alone: that is the artifact author's own styling.
    """
    if not isinstance(value, str):
        return value
    return CONTROL_CHARS.sub("", value)


def scrolling_table(
    items: Sequence[Any],
    cursor: Cursor,
    columns: Sequence[Column],
    row: Callable[[Any], Sequence[RenderableType]],
    height: int,
    console: Console,
    title: str = "",
    empty: str = "nothing to show",
) -> Panel:
    """A table that scrolls with `cursor`. `row` turns one item into cells
    — styling stays yours, this only handles windowing, selection, and
    dropping columns that don't fit.

    `height` is the total space the table may occupy, so it can be passed
    straight from `remaining_height`; the panel border and column header
    come out of it here, where their cost is known. Given less than the
    chrome costs, it collapses to a single line rather than overflowing —
    a short terminal loses the table, not the footer below it."""
    if height <= 0:
        return Group()      # nothing left; occupying a line would overflow
    if height < TABLE_CHROME + 1:
        return Text(f"{len(items)} {title.lower() or 'items'} — window too short", style="dim")
    keep = [c for c in columns if c.drop_below is None or console.size.width >= c.drop_below]
    dropped = {i for i, c in enumerate(columns) if c not in keep}

    # An unbounded flexible column makes rich's ratio-shrink misbehave once
    # a cell's content is far wider than the console: it can zero out the
    # smallest *fixed* column instead of clamping the flexible one (a "#"
    # column vanishing entirely). Give flexible columns an explicit
    # max_width sized to what's actually left over.
    fixed_total = sum(c.width for c in keep if c.width)
    flex_count = sum(1 for c in keep if not c.width) or 1
    overhead = len(keep) * 3 + 4  # column separators + panel border/padding
    flex_max = max(20, (console.size.width - fixed_total - overhead) // flex_count)

    table = Table(expand=True, show_edge=False)
    for col in keep:
        table.add_column(
            col.header,
            justify=col.justify,
            no_wrap=col.no_wrap,
            overflow="ellipsis",
            min_width=col.width,
            max_width=col.width or flex_max,
        )

    if not items:
        table.add_row(Text(empty, style="green"), *([""] * (len(keep) - 1)))
        return Panel(table, title=title)

    window, start = cursor.window(items, height - TABLE_CHROME)
    for i, item in enumerate(window, start=start):
        cells = [plain(c) for j, c in enumerate(row(item)) if j not in dropped]
        table.add_row(*cells, style="reverse" if i == cursor.index else None)

    # Fill the height we were given: the caller already worked out how much
    # room the table may take, so a short list should leave an empty panel of
    # the right size rather than a stub with dead space under it.
    for _ in range(max(0, (height - TABLE_CHROME) - len(window))):
        table.add_row(*([""] * len(keep)))

    span = cursor.range_label(len(items))
    full_title = f"{title} ({span})" if title and span else title or span
    return Panel(table, title=full_title)


def _tile(label: str, value: str) -> Text:
    """Label over value, elided rather than wrapped. A label containing a
    space used to wrap while a single long word ellipsised, so adjacent
    tiles put their values on different rows and the strip stopped reading
    as a row."""
    cell = Text.from_markup(f"[dim]{label}[/]\n[bold]{value}[/]")
    cell.no_wrap = True
    cell.overflow = "ellipsis"
    return cell


def kpi_row(tiles: Sequence[tuple[str, str]], title: str = "") -> RenderableType:
    """A compact row of label/value pairs: two lines, or four with a
    title. A KPI does not need a full-height box each."""
    grid = Table.grid(expand=True, padding=(0, 4))
    for _ in tiles:
        grid.add_column(justify="center")
    grid.add_row(*[_tile(label, value) for label, value in tiles])
    return Panel(grid, title=title) if title else grid


def header(text: str | Text, warn: bool = False, warning: str = "",
           markup: bool = False) -> Text:
    """The status line most artifacts open with — dim normally, yellow with
    an appended note when something's wrong (usually stale data).

    Plain text by default, because `header(f"last error: {msg}")` is the
    common call and data is not markup: a `[a]` in a filename was eaten as
    a tag and a stray `[/]` in a log line killed the render. Pass
    `markup=True` for `"5 errors [red]HIGH[/]"`, or hand it a `Text` you
    styled yourself."""
    base = "yellow" if warn else "dim"
    if isinstance(text, Text):
        # A pre-styled Text keeps its own look, but `warn` is a signal, not
        # a decoration: it used to be a silent no-op on this branch, so the
        # "something is wrong" colour simply vanished for half the API.
        body = text.copy()
        if warn:
            body.stylize(base, 0, len(body))
    else:
        # Base style on the plain runs only. Rich COMBINES a base with each
        # markup span, so `[red]HIGH[/]` under "dim" renders as dim red —
        # the same bleed already fixed in _Timeline's legend, here in the
        # more-used widget. Where you styled it, your style stands alone.
        body = Text.from_markup(text) if markup else Text(text)
        end_of_last = 0
        for span in sorted(body.spans, key=lambda s: s.start):
            if end_of_last < span.start:
                body.stylize(base, end_of_last, span.start)
            end_of_last = max(end_of_last, span.end)
        if end_of_last < len(body):
            body.stylize(base, end_of_last, len(body))
    if warn and warning:
        body.append(f"  ⚠ {warning}", style=base)
    return body


def help_line(*parts: str) -> Text:
    """Assemble the footer from the widgets actually in use — pass
    `Cursor.KEYS` rather than retyping it, so it can't drift from what the
    cursor really handles. `q`/`r` are appended since the loop owns them —
    and de-duplicated, because passing them explicitly is the obvious guess
    and silently printed them twice."""
    shown = dict.fromkeys([*parts, "r refresh", "q quit"])
    return Text(" · ".join(shown), style="dim italic")


def height_of(console: Console, renderable: RenderableType | None) -> int:
    """How many rows `renderable` occupies at this console's width. `None`
    is 0, matching `stack` and `remaining_height` — an optional panel is
    passed around as None, and two sibling helpers disagreeing about it is
    a trap rather than a decision."""
    if renderable is None:
        return 0
    return len(console.render_lines(renderable, console.options, pad=False))


def remaining_height(console: Console, *fixed: RenderableType | int | None) -> int:
    """Rows left for a scrolling region after everything fixed above it.
    rich's `screen=True` Live crops rather than paginating, so anything
    below an over-tall table silently disappears — budget explicitly.

    Pass the renderables themselves; each is measured. Hand-counted row
    totals go wrong the moment a `row()` wraps on a narrow terminal, and
    the symptom is content cropped off the bottom. Plain ints still work
    for anything you'd rather count yourself, and `None` costs nothing —
    so an optional panel can be passed straight through, as to `stack`.

    Returns 0 rather than a floor when nothing is left. It used to floor at
    MIN_VISIBLE_ROWS while `scrolling_table` floored again on top, and the
    two compounded into a frame taller than the terminal — the exact silent
    cropping this function exists to prevent."""
    used = sum(
        f if isinstance(f, int) else height_of(console, f)
        for f in fixed if f is not None
    )
    return max(0, console.size.height - used)


def stack(*renderables: RenderableType | None) -> Group:
    """Vertical stack, skipping Nones — so optional panels can be inlined
    without building the list conditionally."""
    return Group(*[r for r in renderables if r is not None])


def spark_text(values: Sequence[float | None], style: str = "", gap: str = " ") -> Text:
    """Series as block characters, scaled between its own min and max."""
    # A non-finite value is a gap, not a height. json.dump writes NaN by
    # default, so any rate guarded with x/0 arrives here and used to crash
    # render while the data file looked fine on disk.
    values = [v if v is None or isfinite(v) else None for v in values]
    real = [v for v in values if v is not None]
    lo, hi = (min(real), max(real)) if real else (0.0, 0.0)
    span = hi - lo
    if not isfinite(span):
        span = 0.0        # a range wider than a float can hold has no shape
    # No shape to show, but "flat at zero" and "flat at five" are different
    # facts and rendered identically — a steadily-active series looked dead.
    flat_level = 0 if not span and not hi else len(SPARK_CHARS) // 2
    out = Text()
    for value in values:
        if value is None:
            out.append(gap, style="dim")
            continue
        level = int((value - lo) / span * (len(SPARK_CHARS) - 1)) if span else flat_level
        out.append(SPARK_CHARS[min(level, len(SPARK_CHARS) - 1)], style=style)
    return out


class _Trend:
    """A one-line time series that fills whatever width it's given and keeps
    the RECENT end.

    Rich crops and wraps at the right, so a series wider than its panel
    loses the newest buckets — the ones you were looking at. This trims
    from the left instead: fewer days of history, never a hidden today.

    `label` sits before the bar, `summary` after; the bar takes the rest.
    When the bar would starve, the summary goes first, then the label —
    a squeezed panel shows a shorter series rather than three characters
    of one plus a caption.
    """

    def __init__(
        self,
        values: Sequence[float | None],
        *,
        summary: str = "",
        label: str = "",
        style: str = "",
        label_style: str = "dim",
        summary_style: str = "",
    ):
        self.values = list(values)
        self.summary = summary
        self.label = label
        self.style = style
        self.label_style = label_style
        self.summary_style = summary_style

    def _span(self, width: int, label: str, summary: str) -> int:
        return width - (len(label) + 2 if label else 0) - (len(summary) + 2 if summary else 0)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        label, summary = self.label, self.summary
        if self._span(options.max_width, label, summary) < MIN_SPARK:
            summary = ""
        if self._span(options.max_width, label, summary) < MIN_SPARK:
            label = ""
        span = max(1, self._span(options.max_width, label, summary))

        line = Text(no_wrap=True, overflow="crop")
        if label:
            line.append(f"{label}  ", style=self.label_style)
        line.append_text(spark_text(self.values[-span:], self.style))
        if summary:
            line.append(f"  {summary}", style=self.summary_style)
        yield line

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        return Measurement(MIN_SPARK, options.max_width)


def trend(values: Sequence[float | None], **kwargs: Any) -> _Trend:
    return _Trend(values, **kwargs)


class _Row:
    """Panels side by side, wrapping to more lines — and finally to a single
    column — rather than squeezing each below `min_width`.

    A three-panel strip is a three-panel strip on a wide terminal and a
    stack on a narrow one, with no width checks at the call site. Nones are
    dropped, so optional panels can be passed inline.

    Stacking costs height, and a terminal has none to spare. `drop_below`
    marks a row as detail: under that width it disappears rather than
    pushing the rest off the bottom of the screen.
    """

    def __init__(
        self,
        cells: Sequence[RenderableType | None],
        min_width: int = MIN_PANEL_WIDTH,
        drop_below: int | None = None,
    ):
        self.cells = [c for c in cells if c is not None]
        self.min_width = min_width
        self.drop_below = drop_below

    def _per_line(self, width: int) -> int:
        fit = max(1, min(len(self.cells), width // self.min_width))
        lines = -(-len(self.cells) // fit)          # balance: 4 cells over 2
        return -(-len(self.cells) // lines)         # lines is 2+2, not 3+1

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        if not self.cells or (self.drop_below and options.max_width < self.drop_below):
            return
        per_line = self._per_line(options.max_width)
        for start in range(0, len(self.cells), per_line):
            chunk = self.cells[start : start + per_line]
            grid = Table.grid(expand=True, padding=(0, 1))
            for _ in chunk:
                grid.add_column(ratio=1)
            grid.add_row(*chunk)
            yield grid


def row(
    *cells: RenderableType | None,
    min_width: int = MIN_PANEL_WIDTH,
    drop_below: int | None = None,
) -> _Row:
    return _Row(cells, min_width=min_width, drop_below=drop_below)


@dataclass
class _Timeline:
    """A thin one-cell-per-bucket status strip — uptime bars, run history,
    coverage over time. `cells` is one entry per bucket in chronological
    order; `None` means nothing happened in that bucket (rendered with
    `empty_char`), which is usually the interesting signal: a gap.

    Too many buckets for the width and the oldest are dropped, same as
    `_Trend`: the right edge is now, and now is what you came for.
    """

    cells: Sequence[Any]
    style_for: Callable[[Any], str]
    char: str = "█"
    empty_char: str = "·"
    empty_style: str = "dim"
    left_label: str = ""
    right_label: str = ""
    legend: Sequence[tuple[str, str]] = ()

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        shown = list(self.cells)[-options.max_width :]
        n = len(shown) or 1
        # Stretch buckets to fill the panel instead of rendering one column
        # per bucket — a 24-bucket strip in an 80-column panel should read
        # as a wide bar, not a short one hugging the left edge. Extra
        # columns go to the most-recent buckets so "now" stays sharp.
        width = max(options.max_width, n)
        per, extra = divmod(width, n)
        bar = Text(no_wrap=True, overflow="crop")
        for i, cell in enumerate(shown):
            count = per + (1 if i >= n - extra else 0)
            char = self.empty_char if cell is None else self.char
            style = self.empty_style if cell is None else self.style_for(cell)
            bar.append(char * count, style=style)
        yield bar

        if self.left_label or self.right_label:
            pad = " " * max(1, width - len(self.left_label) - len(self.right_label))
            yield Text(f"{self.left_label}{pad}{self.right_label}", style="dim", overflow="crop")

        if self.legend:
            # No base "dim" style on the Text itself — Rich combines a
            # base style with span styles, so a base "dim" muddies every
            # swatch (e.g. "green" renders as "dim green", a duller shade
            # than the bar's plain "green"). Apply "dim" only to the
            # separator/label text so swatches match the bar exactly.
            key = Text(no_wrap=True, overflow="ellipsis")
            for i, (label, style) in enumerate(self.legend):
                if i:
                    key.append("  ", style="dim")
                key.append(self.char, style=style)
                key.append(f" {label}", style="dim")
            yield key

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        return Measurement(min(MIN_SPARK, len(self.cells) or 1), options.max_width)


def timeline(
    cells: Sequence[Any],
    style_for: Callable[[Any], str],
    *,
    title: str = "",
    **kwargs: Any,
) -> RenderableType:
    """A `_Timeline`, wrapped in a panel when you pass `title` — otherwise
    bare, to drop straight into a stack."""
    strip = _Timeline(cells, style_for, **kwargs)
    return Panel(strip, title=title) if title else strip
