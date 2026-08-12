"""The small modules everything else leans on: terminal teardown and
pane detection."""

import threading

import pytest
import termios
from unittest import mock

from tartifacts import registry, terminal


# --- terminal --------------------------------------------------------------

def test_raw_mode_no_ops_without_a_tty(monkeypatch):
    # Headless runs (CI, pipes, cron) must not try to configure a terminal.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with terminal.raw_mode() as active:
        assert active is False


def test_teardown_survives_a_dead_tty(monkeypatch):
    """tcsetattr raises EIO when the tty went away or we've been
    backgrounded. That turned an ordinary exit into a traceback in the
    scrollback; cleanup must swallow it."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdin.fileno", lambda: 0)
    monkeypatch.setattr(termios, "tcgetattr", lambda fd: [])
    monkeypatch.setattr("tty.setcbreak", lambda fd: None)

    def explode(*_args):
        raise termios.error(5, "Input/output error")

    monkeypatch.setattr(termios, "tcsetattr", explode)

    with terminal.raw_mode() as active:      # must not raise on exit
        assert active is True


def test_a_real_error_inside_the_block_still_propagates(monkeypatch):
    # Swallowing teardown errors must not swallow the caller's.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    try:
        with terminal.raw_mode():
            raise ValueError("from the body")
    except ValueError as e:
        assert str(e) == "from the body"
    else:
        raise AssertionError("the body's exception was swallowed")


def test_no_multiplexer_means_no_pane(monkeypatch):
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    assert registry.current_pane() is None


def test_innermost_multiplexer_wins(monkeypatch):
    # tmux inside a herdr pane: the tmux pane is the one actually running us.
    monkeypatch.setenv("HERDR_PANE_ID", "wA:pG")
    monkeypatch.setenv("TMUX_PANE", "%3")
    assert registry.current_pane() == "%3"


def test_herdr_is_used_when_tmux_is_absent(monkeypatch):
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setenv("HERDR_PANE_ID", "wA:pG")
    assert registry.current_pane() == "wA:pG"


def test_pane_detection_needs_no_binary(monkeypatch):
    """It reads an env var — no herdr/tmux on PATH required. Guards the
    regression of shelling out again."""
    monkeypatch.setenv("TMUX_PANE", "%1")
    with mock.patch("subprocess.run", side_effect=AssertionError("must not shell out")):
        assert registry.current_pane() == "%1"


# --- terminal restore ------------------------------------------------------
# Every observable effect is captured so a mutation to any of them fails:
# entering cbreak, restoring termios, emitting the screen-reset escapes on a
# signal, chaining to the artifact's handler, and re-raising. An earlier
# version of this fixture stubbed `tty.setcbreak` to a no-op nobody
# asserted on, so deleting cbreak entirely — the core interaction of the
# whole product — passed the suite.

import signal as _signal


class FakeSignals:
    """A faithful model of `signal.signal`: installing a handler returns the
    PRIOR one, starting from a per-signal sentinel we can recognise. The old
    mocks returned the handler being installed, which made `on_fatal`'s chain
    call itself and recurse."""

    def __init__(self):
        self.current = {}          # signum -> handler currently installed
        self.prior = {}            # a distinct sentinel per signum
        self.reraised = []

    def signal(self, signum, handler):
        was = self.current.get(signum, self.prior.setdefault(signum, ("dfl", signum)))
        self.current[signum] = handler
        return was

    def raise_signal(self, signum):
        self.reraised.append(signum)

    def installed(self, signum):
        return self.current[signum]


@pytest.fixture
def fake_tty(monkeypatch):
    """A tty whose every side effect is recorded, plus a faithful signal model."""
    fd = 7
    obs = {"cbreak": [], "restored": [], "written": b"", "sig": FakeSignals()}
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdin.fileno", lambda: fd)
    monkeypatch.setattr(termios, "tcgetattr", lambda f: ["saved"])
    monkeypatch.setattr("tty.setcbreak", lambda f: obs["cbreak"].append(f))
    monkeypatch.setattr(termios, "tcsetattr", lambda f, when, what: obs["restored"].append(what))

    def record_write(f, data):
        obs["written"] += data
        return len(data)

    monkeypatch.setattr(terminal.os, "write", record_write)
    monkeypatch.setattr(_signal, "signal", obs["sig"].signal)
    monkeypatch.setattr(_signal, "raise_signal", obs["sig"].raise_signal)
    return obs


def test_raw_mode_actually_enters_cbreak(fake_tty):
    """Deleting `tty.setcbreak` left the keyboard line-buffered with echo on:
    j/q do nothing until Enter, and typed characters paint over the frame."""
    with terminal.raw_mode() as active:
        assert active is True
        assert fake_tty["cbreak"] == [7]            # entered, on the real fd


def test_the_signal_handler_restores_before_the_block_ends(fake_tty):
    """Asserted INSIDE the block, so `finally` cannot cover for it."""
    with terminal.raw_mode():
        assert fake_tty["restored"] == []               # nothing restored yet
        fake_tty["sig"].installed(_signal.SIGTERM)(_signal.SIGTERM, None)
        assert fake_tty["restored"] == [["saved"]]      # the handler did it, not finally


def test_a_fatal_signal_leaves_the_alternate_screen_and_shows_the_cursor(fake_tty):
    """The half the old fix missed: `Live.__exit__` never runs on a signal,
    so without this the user is left on a frozen alternate screen with an
    invisible cursor that `stty sane` does not fix."""
    with terminal.raw_mode():
        fake_tty["sig"].installed(_signal.SIGTERM)(_signal.SIGTERM, None)
    assert terminal.EXIT_ALT_SCREEN in fake_tty["written"]
    assert terminal.SHOW_CURSOR in fake_tty["written"]


def test_a_fatal_signal_chains_to_the_artifacts_handler_then_re_raises(fake_tty):
    """SIG_DFL used to drop a flush/close hook the artifact installed. The
    order matters: the artifact's cleanup runs, THEN we die with the default
    action."""
    order = []
    fake_tty["sig"].prior[_signal.SIGTERM] = lambda s, f: order.append("artifact")

    with terminal.raw_mode():
        fake_tty["sig"].installed(_signal.SIGTERM)(_signal.SIGTERM, None)
        order.append("reraised" if fake_tty["sig"].reraised else "!missing")
    assert order == ["artifact", "reraised"], order


def test_every_fatal_signal_is_handled(fake_tty):
    """SIGHUP is the one that fires when a pane or ssh session closes."""
    with terminal.raw_mode():
        pass
    for signum in (_signal.SIGTERM, _signal.SIGHUP, _signal.SIGQUIT):
        assert signum in fake_tty["sig"].current


def test_previous_signal_handlers_are_put_back(fake_tty):
    sentinel = fake_tty["sig"].prior.setdefault(_signal.SIGTERM, ("dfl", _signal.SIGTERM))
    with terminal.raw_mode():
        pass
    # After the block, SIGTERM's handler is the sentinel we started with,
    # not the on_fatal that was installed during the block.
    assert fake_tty["sig"].current[_signal.SIGTERM] == sentinel


def test_raw_mode_degrades_instead_of_crashing_off_the_main_thread(monkeypatch):
    """`signal.signal` raises ValueError outside the main thread. Without the
    guard, an artifact driven from a worker thread crashes on entry; with it,
    the block still runs and `finally` still restores — only the fatal-signal
    path is lost, which is what the module docstring promises.

    Uses the REAL signal module: the ValueError is the thing under test, so
    a fake that never raises would prove nothing.
    """
    entered, restored = [], []
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdin.fileno", lambda: 0)
    monkeypatch.setattr(termios, "tcgetattr", lambda fd: ["saved"])
    monkeypatch.setattr("tty.setcbreak", lambda fd: entered.append(fd))
    monkeypatch.setattr(termios, "tcsetattr", lambda fd, when, what: restored.append(what))

    outcome = {}

    def worker():
        try:
            with terminal.raw_mode() as active:
                outcome["active"] = active
        except BaseException as bad:      # noqa: BLE001 - reporting it IS the test
            outcome["error"] = bad

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)

    assert "error" not in outcome, outcome.get("error")
    assert outcome["active"] is True      # cbreak still entered
    assert entered == [0]
    assert restored == [["saved"]]        # and the tty still restored on exit
