"""Background polling for a JSON file, by mtime. Spawns a daemon thread and pushes
`SourceEvent`s onto a shared `queue.Queue` — the dashboard's main loop
drains it and redraws, no separate extraction step forced on it here.
This is what makes the event loop "reactive": a source pushes the moment
it has something new, instead of the main loop polling on a fixed clock.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from queue import Queue
from typing import Any

FILE_POLL_INTERVAL = 0.5


@dataclass
class SourceEvent:
    ok: bool
    value: Any  # parsed JSON on success, an error message string on failure


def watch_file(path: str, q: Queue, poll_interval: float = FILE_POLL_INTERVAL) -> threading.Event:
    """Polls a JSON file's mtime; re-reads and re-parses on change.
    Returns a `threading.Event` — `.set()` it to force an immediate
    re-check instead of waiting out the poll interval (wire to 'r')."""
    trigger = threading.Event()

    def loop() -> None:
        last_mtime = None
        while True:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = None
            if mtime is not None and mtime != last_mtime:
                last_mtime = mtime
                _send_file(q, path)
            trigger.wait(poll_interval)
            trigger.clear()

    threading.Thread(target=loop, daemon=True).start()
    return trigger


def _send_file(q: Queue, path: str) -> None:
    try:
        with open(path) as f:
            text = f.read()
    except OSError as e:
        q.put(SourceEvent(False, str(e)))
        return
    _send_parsed(q, text)


def _send_parsed(q: Queue, text: str) -> None:
    try:
        q.put(SourceEvent(True, json.loads(text)))
    except json.JSONDecodeError as e:
        q.put(SourceEvent(False, str(e)))
