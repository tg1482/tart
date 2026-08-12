"""Raw-mode keyboard input. Every keypress comes back as a `Key`, byte for
byte — no buffering, no interpretation. What a key *means* depends on the
the artifact does with a key is its own business, not this module's.

Reads the file descriptor directly rather than through `sys.stdin`. That is
not a micro-optimisation: `sys.stdin.read(1)` goes through a buffered
TextIOWrapper, which drains everything available on the fd into a userspace
buffer and hands back one character. The escape-sequence peek then asked
`select` about the *fd*, saw nothing, and concluded "lone Escape" — so
arrow keys never arrived (they were documented in four places), a paste
kept only its first character, and the stranded bytes fired later, out of
order, on the next keypress. Reading the fd keeps `select` and the reader
looking at the same place.

(An earlier version buffered a `{`-prefixed line into a `Line` event for an
agent-push protocol that nothing ever consumed. It was dead code, and it
actively broke text entry — typing a literal `{` silently swallowed
everything after it. Agents push by writing the data file instead, which
a `FileSource` already picks up.)
"""

from __future__ import annotations

import os
import select
import sys
from dataclasses import dataclass

UP = "\x1b[A"
DOWN = "\x1b[B"
RIGHT = "\x1b[C"
LEFT = "\x1b[D"
ESC = "\x1b"
CTRL_C = "\x03"

# How long to wait for the rest of an escape sequence. A real arrow key
# arrives as one burst; a human pressing Escape cannot produce the tail.
ESCAPE_TAIL_WAIT = 0.02


@dataclass
class Key:
    value: str  # one printable char, or one of the constants above


class Reader:
    def __init__(self, stream=None):
        self.stream = stream or sys.stdin

    def _fd(self) -> int:
        return self.stream.fileno()

    def _read(self, count: int) -> str:
        """Straight off the descriptor, so nothing hides in a buffer."""
        try:
            return os.read(self._fd(), count).decode(errors="replace")
        except OSError:
            return ""

    def poll(self, timeout: float) -> Key | None:
        """One keypress within `timeout` seconds, None if idle — and None at
        EOF, which is not a keypress. Returning `Key("")` there left the loop
        spinning at 100% CPU dispatching empty keys forever."""
        ready, _, _ = select.select([self._fd()], [], [], timeout)
        if not ready:
            return None
        first = self._read(1)
        if not first:
            return None  # EOF: the descriptor is done, not silent

        if first != ESC:
            return Key(first)

        # Arrow keys arrive as one burst; a lone Escape doesn't. Both the
        # peek and the read now look at the descriptor, so a tail that
        # exists is found and one that doesn't isn't invented.
        ready, _, _ = select.select([self._fd()], [], [], ESCAPE_TAIL_WAIT)
        if not ready:
            return Key(ESC)
        return Key(first + self._read(2))


def is_quit(key: Key) -> bool:
    """`q` or Ctrl-C. A dashboard is read-only, so a key never means text
    and this needs no mode to disambiguate it."""
    return key.value in ("q", CTRL_C)
