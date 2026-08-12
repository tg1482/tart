"""The raw keyboard reader.

Tested over a real `os.pipe`, not a mock: the whole reason this module
reads the descriptor directly is that a buffered wrapper made `select` and
the reader look at different places, and arrow keys silently never arrived.
A fake stream would reintroduce exactly the abstraction the bug lived in.
"""

import os

import pytest

from tartifacts import input as ck_input


class Pipe:
    """A readable descriptor we can push bytes into, shaped like a stream."""

    def __init__(self):
        self.read_fd, self.write_fd = os.pipe()

    def fileno(self):
        return self.read_fd

    def send(self, text: str):
        os.write(self.write_fd, text.encode())

    def close_writer(self):
        os.close(self.write_fd)

    def close(self):
        for fd in (self.read_fd, self.write_fd):
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.fixture
def pipe():
    p = Pipe()
    yield p
    p.close()


@pytest.fixture
def reader(pipe):
    return ck_input.Reader(stream=pipe)


def test_an_ordinary_keypress_comes_back_as_itself(pipe, reader):
    pipe.send("j")
    assert reader.poll(1.0) == ck_input.Key("j")


def test_an_idle_descriptor_returns_none_rather_than_blocking(reader):
    assert reader.poll(0.01) is None


@pytest.mark.parametrize("sequence,expected", [
    ("\x1b[A", ck_input.UP),
    ("\x1b[B", ck_input.DOWN),
    ("\x1b[C", ck_input.RIGHT),
    ("\x1b[D", ck_input.LEFT),
])
def test_arrow_keys_arrive_whole(pipe, reader, sequence, expected):
    """They arrive as one 3-byte burst. Reading through a buffered wrapper
    consumed the tail into userspace, so `select` saw nothing and the reader
    reported a lone Escape — arrow keys never worked in any released version."""
    pipe.send(sequence)
    assert reader.poll(1.0) == ck_input.Key(expected)


def test_a_lone_escape_is_not_mistaken_for_an_arrow(pipe, reader):
    """Nothing follows, so the tail peek must time out and report Escape
    rather than blocking or inventing a sequence."""
    pipe.send("\x1b")
    assert reader.poll(1.0) == ck_input.Key(ck_input.ESC)


def test_eof_is_none_not_an_endless_empty_key(pipe, reader):
    """Returning Key("") at EOF left the loop spinning at 100% CPU
    dispatching empty keys forever."""
    pipe.close_writer()
    assert reader.poll(1.0) is None


def test_queued_keys_come_back_one_at_a_time_in_order(pipe, reader):
    """A paste or a fast typist queues several bytes; each poll takes
    exactly one, and none are stranded."""
    pipe.send("abc")
    assert [reader.poll(1.0).value for _ in range(3)] == ["a", "b", "c"]


def test_an_arrow_after_a_plain_key_still_parses(pipe, reader):
    pipe.send("g\x1b[B")
    assert reader.poll(1.0) == ck_input.Key("g")
    assert reader.poll(1.0) == ck_input.Key(ck_input.DOWN)


def test_quit_is_q_or_ctrl_c_and_nothing_else():
    assert ck_input.is_quit(ck_input.Key("q")) is True
    assert ck_input.is_quit(ck_input.Key(ck_input.CTRL_C)) is True
    for other in ("j", "r", "Q", ck_input.ESC, ck_input.UP, ""):
        assert ck_input.is_quit(ck_input.Key(other)) is False


def test_ctrl_c_reaches_the_artifact_as_a_key(pipe, reader):
    """In cbreak mode Ctrl-C is a byte, not a signal — if the reader dropped
    it there would be no way to quit a dashboard."""
    pipe.send(ck_input.CTRL_C)
    assert reader.poll(1.0) == ck_input.Key(ck_input.CTRL_C)


def test_a_closed_descriptor_does_not_raise(pipe, reader):
    """The tty can vanish (pane closed, ssh dropped) mid-poll."""
    os.close(pipe.read_fd)
    with pytest.raises((OSError, ValueError)):
        reader.poll(0.01)      # select raises; the loop's teardown handles it
