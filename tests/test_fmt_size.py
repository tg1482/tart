"""Byte formatting, because every size dashboard was writing its own."""

import pytest

from tartifacts import fmt


@pytest.mark.parametrize("value, expected", [
    (0, "0 B"),
    (512, "512 B"),
    (1024, "1 KB"),
    (1536, "1.5 KB"),
    (1024 ** 2, "1 MB"),
    (int(1024 ** 3 * 2.5), "2.5 GB"),
    (1024 ** 5, "1 PB"),
    (1024 ** 6, "1024 PB"),        # runs out of units rather than inventing one
    (-2048, "-2 KB"),
])
def test_size(value, expected):
    assert fmt.size(value) == expected
