from rich.console import Console
from rich.panel import Panel
import pytest
from rich.text import Text

from tartifacts import input as ck_input
from tartifacts import widgets

ROWS = list(range(100))


def lines_of(renderable, width, height=40):
    console = Console(width=width, height=height)
    return ["".join(seg.text for seg in line).rstrip()
            for line in console.render_lines(renderable, console.options, pad=False)]


def test_cursor_reports_whether_it_consumed_the_key():
    # The whole contract behind key composition: a widget must say whether
    # it took the key, or a dashboard's own bindings get silently shadowed.
    c = widgets.Cursor()
    assert c.handle("j", total=10) is True
    assert c.handle("d", total=10) is False
    assert c.handle("s", total=10) is False


def test_cursor_moves_and_clamps_at_both_ends():
    c = widgets.Cursor()
    c.handle("k", total=10)
    assert c.index == 0  # can't go above the first row
    for _ in range(20):
        c.handle("j", total=10)
    assert c.index == 9  # can't go past the last


def test_cursor_jump_keys():
    c = widgets.Cursor()
    c.handle("G", total=50)
    assert c.index == 49
    c.handle("g", total=50)
    assert c.index == 0


def test_cursor_paging_uses_last_rendered_viewport():
    c = widgets.Cursor()
    c.window(ROWS, height=10)  # render sets the viewport
    c.handle("\x04", total=len(ROWS))  # ctrl-d
    assert c.index == 10
    c.handle("\x15", total=len(ROWS))  # ctrl-u
    assert c.index == 0


def test_window_scrolls_to_keep_selection_visible():
    c = widgets.Cursor()
    c.index = 25
    window, start = c.window(ROWS, height=10)
    assert len(window) == 10
    assert start <= 25 < start + 10


def test_window_clamps_index_when_rows_shrink():
    # Data refreshes can shrink the list under a cursor sitting past the end.
    c = widgets.Cursor()
    c.index = 90
    c.window(list(range(5)), height=10)
    assert c.index == 4


def test_window_handles_empty_rows():
    c = widgets.Cursor()
    c.index = 7
    window, start = c.window([], height=10)
    assert (window, start, c.index, c.offset) == ([], 0, 0, 0)


def test_cursor_swallows_nav_keys_even_with_no_rows():
    # Otherwise j/k would fall through to a dashboard binding on an empty
    # list and do something surprising.
    c = widgets.Cursor()
    assert c.handle("j", total=0) is True
    assert c.handle(ck_input.UP, total=0) is True
    assert c.handle("d", total=0) is False


def test_range_label_only_appears_when_scrolling():
    c = widgets.Cursor()
    c.window(ROWS, height=10)
    assert c.range_label(100) == "1-10 of 100"
    c.window(list(range(5)), height=10)
    assert c.range_label(5) == ""


def test_help_line_appends_universal_keys():
    line = widgets.help_line("d detail").plain
    assert "d detail" in line and "r refresh" in line and "q quit" in line


def test_stack_drops_nones():
    assert len(widgets.stack("a", None, "b").renderables) == 2


def test_flexible_column_is_bounded_so_fixed_ones_survive():
    # rich's ratio-shrink will zero out a small fixed column (e.g. "#") when
    # a flexible column's content is far wider than the console. Regression
    # test: every declared column must still get real width.
    from rich.console import Console

    console = Console(width=100, height=40)
    columns = [
        widgets.Column("Service", width=16),
        widgets.Column("#", width=4, justify="right"),
        widgets.Column("Sev", width=10),
        widgets.Column("Issue"),  # flexible, content much wider than console
    ]
    items = [{"n": i} for i in range(3)]
    panel = widgets.scrolling_table(
        items, widgets.Cursor(), columns,
        lambda it: ["wss-service", str(it["n"]), "high", "x" * 400],
        height=10, console=console,
    )
    # render_lines returns a list of *lines*, each a list of Segments.
    lines = ["".join(seg.text for seg in line) for line in console.render_lines(panel, console.options)]
    header = next(line for line in lines if "Service" in line)
    assert "#" in header  # the narrow fixed column still has room for its header
    assert "Sev" in header


def test_spark_text_scales_between_its_own_min_and_max():
    assert widgets.spark_text([0, 100]).plain == "▁█"
    assert widgets.spark_text([0, 0, 0]).plain == "▁▁▁"      # flat at zero is the floor
    assert widgets.spark_text([5, 5, 5]).plain == "▅▅▅"      # flat but present, not dead
    assert widgets.spark_text([]).plain == ""


def test_spark_text_renders_gaps_for_none():
    assert widgets.spark_text([0, None, 100]).plain == "▁ █"


# A quiet stretch, then a ramp. Asymmetric on purpose: an ascending range
# rescales to the same shape whether you slice the head or the tail, so it
# cannot tell the two apart — this can.
QUIET_THEN_BUSY = [0] * 20 + list(range(1, 21))


def test_a_squeezed_trend_keeps_the_newest_buckets():
    line = lines_of(widgets.trend(QUIET_THEN_BUSY), width=20)[0]
    assert line == widgets.spark_text(QUIET_THEN_BUSY[-20:]).plain   # the ramp
    assert line != widgets.spark_text(QUIET_THEN_BUSY[:20]).plain    # not the quiet stretch
    assert set(line) != {"▁"}


def test_a_trend_never_wraps_to_a_second_line():
    # It used to wrap, which pushed the caption down and broke the height
    # budget of everything below it.
    assert len(lines_of(widgets.trend(list(range(200)), summary="1→9"), width=30)) == 1


def test_a_trend_drops_its_summary_before_starving_the_series():
    wide = lines_of(widgets.trend(list(range(40)), label="Positive", summary="2→7 (peak 9)"), 70)[0]
    assert "Positive" in wide and "(peak 9)" in wide

    tight = lines_of(widgets.trend(list(range(40)), label="Positive", summary="2→7 (peak 9)"), 30)[0]
    assert "Positive" in tight and "(peak 9)" not in tight

    tiny = lines_of(widgets.trend(list(range(40)), label="Positive", summary="2→7 (peak 9)"), 14)[0]
    assert "Positive" not in tiny            # the series is the point, not its caption
    assert len(tiny.strip()) >= widgets.MIN_SPARK


def test_a_trend_shorter_than_its_width_is_not_padded_out():
    """One cell per sample, always. Widening short series to fill the bar was
    tried and reverted: at 12 samples in a 90-column pane each value became an
    18-column block, so a flat series read as a solid progress bar. A short
    series should look short."""
    assert lines_of(widgets.trend([0, 50, 100]), width=60)[0] == "▁▄█"


def test_a_timeline_also_keeps_the_recent_end():
    # Nothing happened for 40 buckets, then activity. Showing the head would
    # render an all-gaps strip and hide every event.
    cells = [None] * 40 + list(range(20))
    line = lines_of(widgets.timeline(cells, lambda c: "green"), width=20)[0]
    assert len(line) == 20                   # trimmed, not wrapped
    assert "·" not in line                   # the gaps are the OLD end


def test_a_timeline_stretches_to_fill_a_wider_panel():
    # Fewer buckets than columns used to render one column per bucket and
    # leave the rest of the panel blank instead of filling it.
    cells = [None] * 18 + ["a"] * 6
    line = lines_of(widgets.timeline(cells, lambda c: "green"), width=100)[0]
    assert len(line) == 100


def test_a_timeline_legend_swatch_matches_the_bar_exactly():
    # A base "dim" style on the legend Text combines with each swatch's own
    # style ("green" -> "dim green"), rendering duller than the plain-style
    # bar cells right above it. Swatches must carry no base style at all.
    console = Console(width=40, color_system="standard")
    t = widgets.timeline([None, "a"], lambda c: "green", legend=[("clean", "green")])
    segments = list(console.render(t, console.options.update(max_width=40)))
    # Bar cells may be stretched to more than one "█" per bucket; match any
    # run of the block char rather than assuming exactly one character.
    block_styles = [s.style for s in segments if s.text.strip("█") == "" and s.text]
    assert len(block_styles) == 2            # one bar cell, one legend swatch
    bar_style, swatch_style = block_styles
    assert str(bar_style) == "green"
    assert str(swatch_style) == "green"      # not "dim green"


# --- responsive panels -----------------------------------------------------

def panels(n):
    return [Panel(Text(f"panel {i}"), title=f"p{i}") for i in range(n)]


def test_a_wide_row_puts_panels_side_by_side():
    rendered = lines_of(widgets.row(*panels(3)), width=120)
    assert len(rendered) == 3                        # one panel tall
    assert all(f"p{i}" in rendered[0] for i in range(3))


def test_a_narrow_row_stacks_instead_of_squeezing():
    rendered = lines_of(widgets.row(*panels(3)), width=40)
    assert len(rendered) == 9                        # three panels stacked
    assert all(any(f"p{i}" in line for line in rendered) for i in range(3))


def test_a_row_wraps_to_balanced_lines():
    # 4 panels that fit 3-across balance as 2+2, not 3+1.
    r = widgets.row(*panels(4), min_width=30)
    assert r._per_line(100) == 2


def test_a_row_drops_nones_so_optional_panels_can_be_inlined():
    rendered = lines_of(widgets.row(panels(1)[0], None, None), width=100)
    assert len(rendered) == 3


def test_an_empty_row_renders_nothing():
    assert lines_of(widgets.row(None, None), width=100) == []


def test_a_detail_row_hides_itself_rather_than_stacking():
    # Stacking costs height a terminal doesn't have; a row of dense detail
    # would push the table and footer off the bottom.
    r = widgets.row(*panels(2), drop_below=80)
    assert len(lines_of(r, width=100)) == 3
    assert lines_of(r, width=60) == []


# --- height budgeting ------------------------------------------------------

def test_remaining_height_measures_renderables_instead_of_counting_rows():
    console = Console(width=120, height=40)
    strip = widgets.row(*panels(3))
    assert widgets.remaining_height(console, strip) == 40 - 3


def test_a_row_that_wraps_shrinks_the_scrolling_region():
    """The reason measuring beats a hand-counted constant: the same
    dashboard reserves 3 rows wide and 9 rows narrow. A fixed number would
    crop the bottom of the table off screen on the narrow terminal."""
    strip = widgets.row(*panels(3))
    wide = widgets.remaining_height(Console(width=120, height=40), strip)
    narrow = widgets.remaining_height(Console(width=40, height=40), strip)
    assert wide == 37 and narrow == 31


def test_remaining_height_still_takes_plain_row_counts():
    console = Console(width=120, height=40)
    assert widgets.remaining_height(console, 5, 2) == 33


def test_remaining_height_ignores_none_like_stack_does():
    # An optional panel is passed through as None; it must cost nothing
    # rather than raise.
    console = Console(width=120, height=40)
    assert widgets.remaining_height(console, panels(1)[0], None) == 37


def test_remaining_height_reports_zero_rather_than_a_floor():
    """It used to floor at MIN_VISIBLE_ROWS while scrolling_table floored
    again on top; the two compounded into a frame taller than the terminal,
    which is the exact silent cropping this function exists to prevent."""
    console = Console(width=120, height=10)
    assert widgets.remaining_height(console, 999) == 0


def test_a_dashboard_fits_a_short_terminal_too():
    # Swept widths at height 40 before, so this whole failure mode was
    # invisible: a real terminal gets short as well as narrow.
    for height in (40, 30, 20, 14, 10, 8, 6):
        console = Console(width=100, height=height)
        head = widgets.header("dash")
        foot = widgets.help_line(widgets.Cursor.KEYS)
        table = widgets.scrolling_table(
            [{"n": i} for i in range(500)], widgets.Cursor(),
            [widgets.Column("N")], lambda it: [str(it["n"])],
            height=widgets.remaining_height(console, head, foot),
            console=console, title="Rows",
        )
        total = widgets.height_of(console, widgets.stack(head, table, foot))
        assert total <= height, f"height {height} overflowed by {total - height}"


def test_a_table_with_no_room_collapses_to_one_line():
    console = Console(width=80, height=40)
    tiny = widgets.scrolling_table(
        [{"n": i} for i in range(9)], widgets.Cursor(),
        [widgets.Column("N")], lambda it: [str(it["n"])],
        height=2, console=console, title="Rows",
    )
    assert widgets.height_of(console, tiny) == 1
    assert "9" in "".join(seg.text for line in console.render_lines(tiny, console.options)
                          for seg in line)


def test_a_table_fits_the_height_it_was_given():
    """`height` is total space, not data rows. It used to be data rows, so
    the panel border and column header were 4 rows nobody budgeted — the
    help line below fell off the bottom of every artifact."""
    console = Console(width=100, height=40)
    table = widgets.scrolling_table(
        [{"n": i} for i in range(500)], widgets.Cursor(),
        [widgets.Column("N")], lambda it: [str(it["n"])],
        height=20, console=console, title="Rows",
    )
    assert widgets.height_of(console, table) == 20


def test_a_whole_dashboard_fits_its_terminal_at_any_width():
    """End to end: header + a reflowing row + table + footer, budgeted by
    measurement, must never exceed the screen — rich's Live crops."""
    for width in (120, 100, 70, 50):
        console = Console(width=width, height=40)
        head = widgets.header("dash · a fairly long status line that wraps when narrow")
        strip = widgets.row(*panels(3))
        foot = widgets.help_line(widgets.Cursor.KEYS)
        table = widgets.scrolling_table(
            [{"n": i} for i in range(500)], widgets.Cursor(),
            [widgets.Column("N")], lambda it: [str(it["n"])],
            height=widgets.remaining_height(console, head, strip, foot),
            console=console, title="Rows",
        )
        total = widgets.height_of(console, widgets.stack(head, strip, table, foot))
        assert total <= 40, f"{width} cols overflowed by {total - 40} rows"


def test_height_of_and_stack_agree_about_none():
    # Two sibling helpers disagreeing about None is a trap, not a decision:
    # non-table layouts measure their own optional panels constantly.
    console = Console(width=80, height=40)
    assert widgets.height_of(console, None) == 0
    assert widgets.remaining_height(console, None) == 40


def test_header_takes_markup_and_a_text():
    # A mode badge or a coloured count was inexpressible through the most
    # used widget in the set: markup printed literally, a Text raised.
    assert widgets.header("5 errors [red]HIGH[/]", markup=True).plain == "5 errors HIGH"
    assert widgets.header("a filename [a].json").plain == "a filename [a].json"  # data, not markup
    assert widgets.header(Text("already styled")).plain == "already styled"
    assert "⚠ stale" in widgets.header("x", warn=True, warning="stale").plain


def test_header_markup_keeps_its_own_colour():
    """A base style COMBINES with each markup span in rich, so `[red]` under
    a base of "dim" rendered as dim red — the bleed already fixed once in
    Timeline's legend."""
    console = Console(width=40, color_system="standard")
    segments = list(console.render(widgets.header("5 errors [red]HIGH[/]", markup=True)))
    styles = {seg.text.strip(): str(seg.style) for seg in segments if seg.text.strip()}
    assert styles["HIGH"] == "red"          # not "dim red"
    assert styles["5 errors"] == "dim"      # the plain run is still dim


def test_selected_survives_a_list_that_shrank_under_the_cursor():
    """The live crash: read the selection for a detail panel BEFORE the
    table renders (the order the docs recommend), after the data shrank.
    items[cursor.index] raises; selected() clamps."""
    c = widgets.Cursor()
    c.window(list(range(50)), height=10)
    c.index = 49
    assert c.selected([1, 2, 3]) == 3           # clamped, not IndexError
    assert c.index == 2


def test_selected_is_none_on_an_empty_list():
    c = widgets.Cursor()
    c.index = 5
    assert c.selected([]) is None


def test_selected_tracks_the_cursor():
    c = widgets.Cursor()
    rows = ["a", "b", "c", "d"]
    c.handle("j", total=len(rows))
    c.handle("j", total=len(rows))
    assert c.selected(rows) == "c"


# --- data-derived strings and numbers are hostile input --------------------
# A fetch script scrapes whatever the world gives it. Both filters below
# guard values that render EVERY frame, and neither had a test.

def test_plain_strips_control_characters_from_scraped_data():
    """A git branch, CI status or log line carrying ANSI reaches the screen
    on every frame — clearing it, or setting the window title."""
    dirty = "main\x1b[2J\x1b]0;pwned\x07 \x1b[31mred\x1b[0m"
    cleaned = widgets.plain(dirty)
    assert "\x1b" not in cleaned and "\x07" not in cleaned
    assert "main" in cleaned and "red" in cleaned


def test_plain_leaves_the_artifacts_own_styling_alone():
    """A Text is the author's own markup, not scraped data."""
    styled = Text("main", style="bold red")
    assert widgets.plain(styled) is styled


def test_spark_text_treats_a_nan_as_a_gap_not_a_crash():
    """`json.dump` writes NaN by default, so any rate guarded with x/0
    arrives here in a file that looks perfectly valid on disk."""
    drawn = widgets.spark_text([1.0, float("nan"), 3.0, float("inf"), 5.0])
    assert len(drawn.plain) == 5                       # one slot per value
    assert drawn.plain.count(" ") == 2                 # nan and inf are gaps
    assert "nan" not in drawn.plain.lower()


def test_spark_text_of_only_nans_still_renders():
    drawn = widgets.spark_text([float("nan"), float("nan")])
    assert drawn.plain is not None


# --- Cursor.window invariants ----------------------------------------------
# window() is a pure function of (rows, height) over the cursor's position,
# so the whole space is worth sweeping. Hand-picked examples never force the
# boundary states, which is why three clamp mutants survived the audit here.

@pytest.mark.parametrize("n", [0, 1, 2, 3, 5, 8, 13])
@pytest.mark.parametrize("height", [-3, 0, 1, 2, 3, 7, 20])
@pytest.mark.parametrize("start", [-5, 0, 1, 4, 12, 99])
def test_window_invariants_hold_at_every_size(n, height, start):
    rows = list(range(n))
    cursor = widgets.Cursor()
    cursor.index = start
    visible, offset = cursor.window(rows, height)
    fits = max(1, height)                       # heights below 1 are meaningless

    assert offset == cursor.offset              # the reported offset is the real one
    assert visible == rows[offset:offset + fits]   # and the slice agrees with it
    assert offset >= 0
    if not rows:
        assert (visible, offset, cursor.index) == ([], 0, 0)
        return
    assert len(visible) == min(fits, n)         # fills the viewport whenever it can
    assert 0 <= cursor.index <= n - 1           # selection clamped into range
    assert offset <= cursor.index < offset + fits    # the cursor is ON SCREEN
    assert offset + len(visible) <= n           # never slices past the end
    assert offset <= max(0, n - fits)           # never scrolls past the last page


@pytest.mark.parametrize("keys", [
    "jjjjjjjjjj", "G", "GkkkG", "\x04\x04\x04", "G\x15\x15", "jjG\x15k",
])
def test_the_cursor_stays_on_screen_however_you_move(keys):
    """Drives the real key handler, re-rendering each frame like the loop
    does. A selection scrolled off screen is invisible-but-active: keys move
    something the user cannot see."""
    rows = list(range(40))
    cursor = widgets.Cursor()
    cursor.window(rows, 6)                      # establish a viewport first
    for key in keys:
        cursor.handle(key, len(rows))
        visible, offset = cursor.window(rows, 6)
        assert rows[cursor.index] in visible
        assert offset <= cursor.index < offset + 6


def test_window_keeps_the_cursor_visible_when_the_pane_shrinks():
    """A terminal resize changes height between frames with the offset left
    over from the old one — the state a single-call test never reaches."""
    rows = list(range(30))
    cursor = widgets.Cursor()
    cursor.index = 25
    cursor.window(rows, 20)                     # tall pane: offset scrolls down
    visible, offset = cursor.window(rows, 3)    # then it shrinks hard
    assert rows[25] in visible
    assert offset <= 25 < offset + 3
    assert len(visible) == 3


def test_window_recovers_when_the_data_shrinks_under_a_scrolled_cursor():
    """The offset survives between frames, so a fetch returning fewer rows
    leaves it pointing past the end — `rows[80:86]` of a 5-row list is an
    EMPTY table with a live cursor, on a dashboard that was fine a second
    ago. Only the upper offset clamp prevents it, and nothing forced that
    state: a fresh cursor can never scroll past the last page on its own."""
    cursor = widgets.Cursor()
    cursor.index = 90
    visible, offset = cursor.window(list(range(100)), 6)
    assert offset > 0                          # genuinely scrolled down

    visible, offset = cursor.window(list(range(5)), 6)   # the fetch dropped rows
    assert visible == list(range(5))           # not an empty table
    assert offset == 0
    assert cursor.index == 4                   # selection pulled back into range


def test_help_line_does_not_repeat_keys_the_caller_also_passed():
    """It appends `r refresh · q quit` itself, so passing them — the obvious
    guess, and what the first artifact written against it did — printed them
    twice. Found by building an example, not by a test."""
    line = widgets.help_line(widgets.Cursor.KEYS, "r refresh", "q quit").plain
    assert line.count("r refresh") == 1
    assert line.count("q quit") == 1
    assert line.endswith("r refresh · q quit")


def test_help_line_keeps_the_callers_order():
    line = widgets.help_line("a", "b").plain
    assert line == "a · b · r refresh · q quit"


def test_range_label_is_empty_until_a_height_has_been_measured():
    """A title is built BEFORE the table renders — the natural order, and
    the one the docs recommend — so a guessed viewport reported "1-3 of 8"
    above a table that was showing all 8 rows."""
    cursor = widgets.Cursor()
    assert cursor.range_label(8) == ""            # nothing measured yet
    cursor.window(list(range(8)), 8)
    assert cursor.range_label(8) == ""            # measured: everything fits
    cursor.window(list(range(20)), 8)
    assert cursor.range_label(20) == "1-8 of 20"  # measured: actually scrolling


def test_paging_still_works_before_the_first_render():
    """ctrl-d can't jump by an unmeasured viewport, so it falls back rather
    than moving zero rows."""
    cursor = widgets.Cursor()
    cursor.handle("\x04", 100)
    assert cursor.index == widgets.MIN_VISIBLE_ROWS



def test_a_long_series_still_keeps_the_recent_end():
    drawn = lines_of(widgets.trend(list(range(200))), width=40)[0]
    assert len(drawn.rstrip()) <= 40        # cropped, not stretched


def test_a_short_table_still_fills_the_height_it_was_given():
    """A 2-row table handed 12 lines used to render 6 and leave dead air
    under it — the artifact looked unfinished rather than quiet."""
    console = Console(width=80, height=40)
    panel = widgets.scrolling_table(
        [{"n": 1}, {"n": 2}], widgets.Cursor(), [widgets.Column("n")],
        lambda r: [str(r["n"])], height=12, console=console, title="T",
    )
    assert widgets.height_of(console, panel) == 12


# --- Keys ------------------------------------------------------------------

def test_keys_dispatches_and_reports_what_it_handled():
    bound = widgets.Keys({"t": ("timescale", lambda st: st.update(scale="7d"))})
    state = {"scale": "24h"}
    assert bound.handle("t", state) is True
    assert state["scale"] == "7d"
    assert bound.handle("z", state) is False      # unbound falls through


def test_keys_help_is_derived_from_the_bindings():
    """The whole point: rename a key and the footer follows. Written by hand
    it was an if/elif plus a matching string, and nothing kept them honest."""
    bound = widgets.Keys({"t": ("timescale", lambda st: None),
                          "d": ("details", lambda st: None)})
    assert bound.help == "t timescale · d details"
    assert widgets.help_line(bound.help).plain == "t timescale · d details · r refresh · q quit"


def test_plain_flattens_newlines_and_tabs_into_single_spaces():
    """A scraped title with an embedded newline forced a second physical
    line inside a no_wrap cell, misaligning the whole row — every fetch
    script was adding its own ' '.join(s.split())."""
    from tartifacts import widgets
    assert widgets.plain("two\nlines\there") == "two lines here"
    assert widgets.plain("  padded   out  ") == "padded out"
    assert widgets.plain("\x1b[31mred\x1b[0m branch\n") == "[31mred[0m branch"
