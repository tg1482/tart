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
import time

from . import envfile, status
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
        # Seeded from disk so a fetch that failed BEFORE launch — cron
        # overnight, `tart run`'s own pre-launch refresh — is visible from
        # the first frame, not only after this keeper's first attempt.
        self.last_fetch: dict | None = status.last_fetch(manifest.path)

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
        """Never raises — the artifact keeps showing stale data with its
        warning rather than dying. But never *silent* either: the outcome
        (exit code, output tail) is recorded whatever happens, because
        "captured and discarded" is how a fetch fails for 15 hours with the
        diagnosis destroyed each time."""
        started = time.time()
        env = dict(os.environ)

        def record(**outcome) -> dict:
            return status.record_fetch(
                self.manifest.path, trigger="keeper", duration=time.time() - started,
                path=env.get("PATH"), **outcome,
            )

        if self.manifest.env_file_path is not None:
            try:
                env.update(envfile.load(self.manifest.env_file_path))
            except OSError as bad:
                self.last_fetch = record(
                    error=f"env_file {self.manifest.env_file_path} cannot be loaded: {bad}"
                )
                return
        env["TART_MANIFEST"] = str(self.manifest.path.resolve())
        try:
            proc = subprocess.run(
                self.manifest.fetch, shell=True, cwd=self.manifest.root, env=env,
                capture_output=True, text=True, timeout=FETCH_TIMEOUT, check=False,
            )
        except subprocess.TimeoutExpired as bad:
            self.last_fetch = record(
                error=f"timed out after {FETCH_TIMEOUT:.0f}s", output=_text(bad.output)
            )
            return
        except OSError as bad:
            self.last_fetch = record(error=str(bad))
            return
        self.last_fetch = record(
            exit_code=proc.returncode,
            output=(proc.stdout or "") + (proc.stderr or ""),
        )


def _text(output: str | bytes | None) -> str:
    """TimeoutExpired carries whatever was captured so far — as bytes."""
    if output is None:
        return ""
    return output.decode(errors="replace") if isinstance(output, bytes) else output
