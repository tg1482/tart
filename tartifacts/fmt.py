"""Number formatting, so artifacts stop each rewriting it.

Pure functions over `format`'s own thousands-grouping — the value here is
one obvious answer to "how do we print money / counts / bytes", not
cleverness.
"""

from __future__ import annotations


def usd(n: float) -> str:
    """Always 2 decimals — a currency value showing "$0" instead of
    "$0.00" reads as a bug, not a rounding choice."""
    sign = "-" if n < 0 else ""
    return f"{sign}${abs(n):,.2f}"


def grouped(n: float) -> str:
    """"5911.0" -> "5,911" (drops the decimals for whole numbers — right
    for counts, wrong for currency, see `usd` above)."""
    sign = "-" if n < 0 else ""
    if float(n).is_integer():
        return f"{sign}{abs(int(n)):,}"
    return f"{sign}{abs(n):,.2f}"


BYTE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def size(n: float) -> str:
    """1536 -> '1.5 KB'. Whole units lose the decimal ('2 MB', not
    '2.0 MB') — a size is read at a glance, not compared digit by digit."""
    sign = "-" if n < 0 else ""
    value, unit = float(abs(n)), 0
    while value >= 1024 and unit < len(BYTE_UNITS) - 1:
        value, unit = value / 1024, unit + 1
    rendered = f"{value:.0f}" if value.is_integer() or unit == 0 else f"{value:.1f}"
    return f"{sign}{rendered} {BYTE_UNITS[unit]}"
