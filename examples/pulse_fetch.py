"""When each project's Claude was actually working, hour by hour.

Same bounded tail-scan as burn_fetch, bucketed by project x hour instead of
summed — the shape of a day rather than its total.
"""

import glob
import json
import os
import time
from collections import defaultdict

import tartifacts

TAIL_BYTES = 3_000_000
WINDOW_HOURS = 48
TRANSCRIPTS = os.path.expanduser("~/.claude/projects/*/*.jsonl")


def label(project: str) -> str:
    """`-Users-tanmaygupta-dev-quarkle-wss-service` -> `wss-service`."""
    trimmed = project.replace("-Users-tanmaygupta-", "", 1).strip("-")
    for prefix in ("dev-quarkle-", "dev-"):
        if trimmed.startswith(prefix):
            trimmed = trimmed[len(prefix):]
    return trimmed or project


now = time.time()
start = now - WINDOW_HOURS * 3600
grid = defaultdict(lambda: [0] * WINDOW_HOURS)
turns = 0

for path in glob.glob(TRANSCRIPTS):
    try:
        if os.path.getmtime(path) < start:
            continue
        project = label(os.path.basename(os.path.dirname(path)))
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > TAIL_BYTES:
                fh.seek(size - TAIL_BYTES)
                fh.readline()
            for line in fh:
                if b'"timestamp"' not in line or b'"usage"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                stamp = entry.get("timestamp")
                if not stamp:
                    continue
                epoch = time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
                hour = int((epoch - start) // 3600)
                if 0 <= hour < WINDOW_HOURS:
                    grid[project][hour] += 1
                    turns += 1
    except OSError:
        continue

rows = sorted(grid.items(), key=lambda kv: -sum(kv[1]))
tartifacts.write_data({
    "at": now,
    "window_hours": WINDOW_HOURS,
    "turns": turns,
    "hour_labels": [
        time.strftime("%H", time.localtime(start + h * 3600)) for h in range(WINDOW_HOURS)
    ],
    "projects": [{"project": name, "hours": hours, "turns": sum(hours)} for name, hours in rows],
    "total_by_hour": [sum(hours[h] for _, hours in rows) for h in range(WINDOW_HOURS)],
})
print(f"{turns} turns across {len(rows)} projects over {WINDOW_HOURS}h")
