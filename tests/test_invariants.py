"""Properties that must hold for ALL inputs, not for the case that motivated
a fix.

Every bug in this file's history was a fix tested against exactly the input
that prompted it: `write_data` was tested with a set and missed NaN; the
`--state` mode guard was tested with "INSERT" and missed 5; the layout was
swept across widths at a fixed height. These search the space instead.
"""

from itertools import product

import pytest
from rich.console import Console
from rich.text import Text

from tartifacts import widgets

WIDTHS = [40, 50, 64, 70, 80, 100, 120, 160]
HEIGHTS = [6, 8, 12, 20, 30, 40]

# A parametrised sweep over an empty list reports "skipped" and exits green,
# so emptying either list silently turns this whole file into a no-op.
assert len(WIDTHS) >= 6 and len(HEIGHTS) >= 4, "the sweep must actually sweep"


# --- data integrity --------------------------------------------------------


def render_lines(renderable, width, height=40):
    console = Console(width=width, height=height)
    return ["".join(s.text for s in line)
            for line in console.render_lines(renderable, console.options, pad=False)]


COLUMNS = [
    widgets.Column("Repo"),
    widgets.Column("Branch", width=20),
    widgets.Column("Sync", width=9),
    widgets.Column("Tree", width=14),
    widgets.Column("Last", width=5),
]
ROW = ["a-fairly-long-repository-name", "main", "^1", "~1 ?3", "13h"]


@pytest.mark.parametrize("width", WIDTHS)
def test_every_declared_column_shows_its_data_or_is_dropped(width):
    """Squeezing a fixed column to a blank sliver deletes data while still
    spending the space, with no ellipsis to signal it."""
    console = Console(width=width)
    table = widgets.scrolling_table(
        [{"r": ROW}], widgets.Cursor(), COLUMNS, lambda it: it["r"],
        height=10, console=console,
    )
    lines = render_lines(table, width)
    header_line = next(line for line in lines if "Repo" in line)
    body = "\n".join(lines)
    for column, cell in zip(COLUMNS, ROW):
        if column.header not in header_line:
            continue                       # dropped outright, which is honest
        # Present in the header means it claimed space, so it owes us data.
        assert cell[:3] in body, f"{width}: '{column.header}' kept its space but shows nothing"


@pytest.mark.parametrize("width,height", list(product(WIDTHS, HEIGHTS)))
def test_a_frame_never_exceeds_its_terminal(width, height):
    console = Console(width=width, height=height)
    head = widgets.header("dash · a status line long enough to wrap when narrow")
    strip = widgets.kpi_row([("Dirty repos", "14"), ("Changed files", "156"),
                             ("Unpushed", "5"), ("Stashed", "144")])
    foot = widgets.help_line(widgets.Cursor.KEYS)
    table = widgets.scrolling_table(
        [{"n": i} for i in range(200)], widgets.Cursor(), [widgets.Column("N")],
        lambda it: [str(it["n"])],
        height=widgets.remaining_height(console, head, strip, foot),
        console=console, title="Rows",
    )
    total = widgets.height_of(console, widgets.stack(head, strip, table, foot))
    assert total <= height, f"{width}x{height}: overflowed by {total - height}"


@pytest.mark.parametrize("width", WIDTHS)
def test_a_kpi_row_keeps_every_value_on_one_line(width):
    """A label with a space wraps while a single word ellipsises, so adjacent
    tiles put their values on different rows and the strip stops reading as
    a row."""
    strip = widgets.kpi_row([("Dirty repos", "14"), ("Changed files", "156"),
                             ("Unpushed", "5"), ("Stashed", "144")])
    lines = [line for line in render_lines(strip, width) if line.strip()]
    values = [line for line in lines if any(v in line for v in ("14", "156", "5", "144"))]
    assert len(values) == 1, f"{width}: values split across {len(values)} lines"


# --- data fidelity ---------------------------------------------------------

def test_distinct_series_render_distinctly():
    """A flat series at 5/day rendered identically to a flat series at 0, so
    a steadily-active repo looked dead."""
    assert widgets.spark_text([0] * 8).plain != widgets.spark_text([5] * 8).plain


@pytest.mark.parametrize("series", [[0] * 5, [3] * 5, [0, 1], [7], []])
def test_a_series_never_renders_wider_than_its_values(series):
    assert len(widgets.spark_text(series).plain) == len(series)


# --- header ----------------------------------------------------------------

@pytest.mark.parametrize("text", ["plain", Text("already a Text")])
def test_warn_is_always_visible(text):
    """warn=True was a silent no-op whenever the caller passed a Text — the
    'something is wrong' signal simply vanished."""
    body = widgets.header(text, warn=True)          # no warning appendix to hide behind
    console = Console(width=80, color_system="standard")
    styles = {str(seg.style) for seg in console.render(body) if seg.text.strip()}
    assert styles, f"{text!r} rendered no styled segments at all"
    assert all("yellow" in style for style in styles), f"{text!r} body not yellow: {styles}"
