"""Write JSON so an interrupted write can't destroy the previous file.

`open(path, "w")` truncates before it writes, so a crash, a full disk or
two processes writing at once leaves a half-written file. Every reader in
tart treats unparseable as empty — which is self-healing for the index,
but for `config.json` it means every configured root silently disappears
and every artifact stops being found, with nothing to point at.

Writing to a temp file in the same directory and `os.replace`-ing it is
atomic on POSIX: readers see either the old file or the new one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _reject_coerced_keys(value: Any) -> None:
    """json silently turns a non-string key into a string, so {1: "a",
    "1": "b"} becomes two identical keys and one value is lost on reparse."""
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise TypeError(f"JSON object keys must be strings, got {key!r}")
            _reject_coerced_keys(nested)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_coerced_keys(item)


def write(path: Path, value: Any, default: Any = None) -> None:
    """Raises OSError like a plain write would — callers that treat their
    file as a disposable cache already swallow it.

    Strict about the value: without `default`, anything JSON can't
    represent raises TypeError. It used to pass `default=str`
    unconditionally, which turned a set, a Decimal or a stray object into a
    quoted string and reported success — `write_data({...})` wrote the
    literal `"{Ellipsis}"` and exited 0. Only summaries, which are
    best-effort display data, opt into stringifying.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        # allow_nan=False, sort_keys: NaN and Infinity are Python, not JSON.
        # They read back fine here and break in every other consumer — or
        # turn silently into null in jq. A rate guarded with x/0 or a numpy
        # aggregate is the likeliest bad value a fetch script produces.
        # skipkeys stays off so a non-string key raises instead of coercing
        # to a duplicate that loses a value on reparse.
        encoded = json.dumps(value, indent=2, default=default, allow_nan=False)
        _reject_coerced_keys(value)
        tmp.write_text(encoded + "\n")
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass  # nothing left to do; the original file is still intact
        raise
