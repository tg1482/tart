"""Keeping an artifact's own data fresh, from the policy its manifest declares.

Without this, `fetch`/`stale_after` are only consulted at launch: an artifact
left open past its staleness limit shows a warning and waits for a human,
and pressing `r` re-reads the same stale file. With `auto_refresh: true`
an artifact maintains itself and needs no external cron at all.

The keeper only runs `fetch` — it doesn't push data anywhere. The
artifact's existing `FileSource` sees the resulting mtime change and
redraws on its own, so there's one path for "data changed" regardless of
who caused it.
"""

from __future__ import annotations

import os
import subprocess
import threading

from .manifest import Manifest

# How often to re-check staleness. A fraction of the limit so an artifact
# doesn't sit visibly stale for long, floored so a short `stale_after`
# can't spin.
MIN_CHECK_INTERVAL = 30.0
CHECK_FRACTION = 0.1

# Ceiling on one fetch, shared with the CLI so a wedged data command behaves
# the same whether a human or the keeper started it.
FETCH_TIMEOUT = 600.0


class Keeper:
    """Runs a manifest's `fetch` when its data goes stale. `force()` runs it
    immediately regardless — that's what `r` is wired to, since an explicit
    refresh should actually refetch, not just re-read."""

    def __init__(self, manifest: Manifest):
        self.manifest = manifest
        self._wake = threading.Event()
        self._force = False
        self._lock = threading.Lock()

    @property
    def can_fetch(self) -> bool:
        return bool(self.manifest.fetch)

    def start(self) -> None:
        """Only meaningful with auto_refresh; `force()` works either way."""
        if self.can_fetch:
            threading.Thread(target=self._loop, daemon=True).start()

    def force(self) -> None:
        if not self.can_fetch:
            return
        with self._lock:
            self._force = True
        self._wake.set()

    def should_fetch(self) -> bool:
        """Auto-refresh means "re-run fetch when the data passes
        `stale_after`". With no `stale_after`, is_stale() is None — and
        treating that as "fetch" re-ran the command every 30s forever,
        output swallowed and nothing surfaced. Missing data is
        still worth fetching: that is the self-heal, not a hammer."""
        if not self.manifest.auto_refresh:
            return False
        if self.manifest.data_path and not self.manifest.data_path.exists():
            return True
        return self.manifest.is_stale() is True

    def _interval(self) -> float:
        limit = self.manifest.stale_after
        return max(MIN_CHECK_INTERVAL, limit * CHECK_FRACTION) if limit else MIN_CHECK_INTERVAL

    def _loop(self) -> None:
        while True:
            self._wake.wait(self._interval())
            self._wake.clear()
            with self._lock:
                forced, self._force = self._force, False
            # is_stale() is None when data is missing or unjudgeable — both
            # are reasons to fetch, so only a definite False means skip.
            if forced or self.should_fetch():
                self._run()

    def _run(self) -> None:
        try:
            subprocess.run(
                self.manifest.fetch, shell=True, cwd=self.manifest.root,
                env={**os.environ, "TART_MANIFEST": str(self.manifest.path.resolve())},
                capture_output=True, timeout=FETCH_TIMEOUT, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass  # the artifact keeps showing stale data with its warning — better than dying
