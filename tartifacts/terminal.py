"""Raw-mode terminal context manager — leave the terminal as we found it.

`with` restores on exception, but a *signal* is not an exception: the
default disposition for SIGTERM/SIGHUP/SIGQUIT terminates the interpreter
without unwinding, so `finally` never runs. Two things then leak:

- the tty stays in cbreak with echo off (fixable with `stty sane`), and
- if the artifact was drawn with rich's `screen=True`, the alternate
  screen and hidden cursor are never undone, because `Live.__exit__`
  doesn't run either — leaving a frozen dashboard and an invisible cursor
  that `stty sane` does NOT fix (you need `tput rmcup; tput cnorm`).

So the fatal-signal handler restores the tty AND emits the escapes that
leave the alternate screen and show the cursor, then chains to whatever
handler the artifact itself installed, then dies with the default action —
so `pkill tart`, a supervisor stop, `tmux kill-pane` and Ctrl-\\ end the
same way they would without us, and the shell is usable afterwards.

Constraints, stated honestly rather than papered over: this is main-thread
only (signals can't be installed elsewhere) and not re-entrant. tart calls
it once, from the main thread, so both hold; if either stops being true the
signal path silently becomes a no-op and only the `finally` protects you.

(Ctrl-C is different and already worked: `tty.setcbreak` leaves ISIG on, so
it arrives as SIGINT, whose default raises KeyboardInterrupt — an exception,
which `finally` does see.)
"""

from __future__ import annotations

import os
import signal
import sys
import termios
import tty
from contextlib import contextmanager

# Signals whose default action kills the process without unwinding.
FATAL_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)

# What a screen=True artifact turned on and a dying process must turn off.
EXIT_ALT_SCREEN = b"\x1b[?1049l"
SHOW_CURSOR = b"\x1b[?25h"


@contextmanager
def raw_mode():
    """Cbreak mode on stdin for the duration of the block — keys arrive
    immediately, unbuffered, no local echo. No-ops if stdin isn't a real
    tty (piped/non-interactive), so a dashboard degrades gracefully
    instead of crashing when run headless."""
    if not sys.stdin.isatty():
        yield False
        return
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    def restore() -> None:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except termios.error:
            # EIO here means the tty went away or we're no longer the
            # foreground process group (backgrounded, pane closed, ssh
            # dropped). Nothing left to restore, and raising from a
            # cleanup path would mask whatever actually ended the run.
            pass

    def on_fatal(signum, frame):
        restore()
        _reset_display(fd)
        # Chain to the artifact's own handler (a flush/close-on-shutdown
        # hook) before dying — SIG_DFL used to drop it silently.
        previous_handler = previous.get(signum)
        if callable(previous_handler):
            previous_handler(signum, frame)
        signal.signal(signum, signal.SIG_DFL)
        signal.raise_signal(signum)      # die exactly as we would have

    previous = {}
    try:
        for signum in FATAL_SIGNALS:
            previous[signum] = signal.signal(signum, on_fatal)
    except ValueError:
        # Off the main thread signals can't be installed, so a fatal signal
        # here will NOT restore the terminal — only the finally below does,
        # on normal or exception exit. tart never does this; the honesty is
        # for whoever moves the call.
        pass

    try:
        tty.setcbreak(fd)
        yield True
    finally:
        restore()
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, TypeError):
                pass


def _reset_display(fd: int) -> None:
    """Undo the alternate screen and cursor-hide directly on the fd. Safe on
    the main screen too (a no-op), so it needn't know whether the artifact
    actually used screen mode."""
    try:
        os.write(fd, EXIT_ALT_SCREEN + SHOW_CURSOR)
    except OSError:
        pass
