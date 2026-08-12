"""The event loop itself.

Everything around `_interactive` was covered and it wasn't, because it
needs a tty — so the ~65 lines that ARE the product (key dispatch, refresh,
redraw on new data, registry lifecycle, teardown) were the least tested
code in the project.

No tty required: the loop's three couplings to the outside are the input
Reader, rich's Live, and raw_mode. Substituting those leaves the real logic
running.
"""

import json
from contextlib import nullcontext

import pytest

from tartifacts import app
from tartifacts import input as ck_input
from tartifacts import registry, terminal, widgets


class ScriptedReader:
    """Plays a list of keys, then holds down quit — and gives up if the loop
    ignores it.

    Without the bound, a loop that stops honouring `q` spins forever and the
    test HANGS instead of failing: CI times out with no diagnosis. Found by
    mutating `is_quit`, which is exactly the kind of break this file exists
    to catch.
    """

    QUIT_ATTEMPTS = 50

    def __init__(self, keys):
        self.keys = list(keys)
        self.quits = 0

    def poll(self, timeout):
        if self.keys:
            return ck_input.Key(self.keys.pop(0))
        self.quits += 1
        if self.quits > self.QUIT_ATTEMPTS:
            raise AssertionError("the loop ignored quit — it would spin forever")
        return ck_input.Key("q")


class FakeLive:
    """Records every frame the loop pushes."""

    def __init__(self, renderable, **kwargs):
        self.frames = [renderable]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, renderable):
        self.frames.append(renderable)


@pytest.fixture
def loop(monkeypatch):
    """Substitutes the loop's three couplings to the outside world and
    hands back a driver. monkeypatch, not assignment: a test that leaves
    `Reader` replaced would silently break every other file."""
    import rich.live

    monkeypatch.setattr(rich.live, "Live", FakeLive)
    monkeypatch.setattr(terminal, "raw_mode", lambda: nullcontext(True))

    def drive(keys, state=None, render=None, on_key=None,
              manifest_path=None, sources=None):
        monkeypatch.setattr(ck_input, "Reader", lambda: ScriptedReader(keys))
        app._interactive(
            "T", manifest_path, sources or {}, dict(state or {}),
            render or (lambda st, console: "frame"),
            app._keys(None, on_key),
        )

    return drive


def test_q_quits(loop):
    loop(["q"])          # returns rather than spinning


def test_a_key_reaches_the_artifacts_handler(loop):
    seen = []
    loop(["a", "b", "q"], on_key=lambda key, st: seen.append(key))
    assert seen == ["a", "b"]


def test_r_forces_every_source_to_recheck(loop):
    triggered = []

    class Source:
        def read_now(self):
            return {"n": 1}

        def start(self, q):
            class Trigger:
                def set(inner):
                    triggered.append(True)
            return Trigger()

    loop(["r", "q"], sources={"data": Source()})
    assert triggered == [True]


def test_a_frame_is_rendered_per_iteration(loop):
    frames = []
    loop(["a", "q"], render=lambda st, console: frames.append(1) or "f")
    assert len(frames) >= 2       # initial paint plus at least one redraw


def test_the_registry_entry_is_removed_on_exit(loop, tmp_path):
    spec = tmp_path / "x.json"
    spec.write_text(json.dumps({"title": "X", "run": "true"}))
    loop(["q"], manifest_path=str(spec))
    assert registry.live() == []


def test_the_registry_is_cleaned_up_even_when_render_raises(loop, tmp_path):
    """A crash mid-loop must not leave a phantom in `tart list`."""
    spec = tmp_path / "x.json"
    spec.write_text(json.dumps({"title": "X", "run": "true"}))

    def explode(state, console):
        raise ValueError("boom")

    with pytest.raises(ValueError):
        loop(["q"], manifest_path=str(spec), render=explode)
    assert registry.live() == []




def test_declared_keys_run_before_on_key_and_stop_it(loop):
    """Bindings claim their key; on_key only sees what's left."""
    leftovers = []
    bound = widgets.Keys({"t": ("timescale", lambda st: st.update(scale="7d"))})
    state = {"scale": "24h"}
    app._keys(None, lambda key, st: leftovers.append(key), bound)("t", state)
    app._keys(None, lambda key, st: leftovers.append(key), bound)("z", state)
    assert state["scale"] == "7d"
    assert leftovers == ["z"]


def test_the_cursor_still_wins_over_a_declared_key(loop):
    """A binding must not steal j/k from the cursor."""
    bound = widgets.Keys({"j": ("wrong", lambda st: st.update(stolen=True))})
    state = {"cursor": widgets.Cursor()}
    app._keys(lambda st: [1, 2, 3], None, bound)("j", state)
    assert "stolen" not in state
    assert state["cursor"].index == 1
