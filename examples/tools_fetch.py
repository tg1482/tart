"""Which tools the Claudes actually reach for, and which ones fail.

Errors arrive as a separate `tool_result` block referencing a `tool_use_id`,
so a tool's failure rate can only be built by joining the two — which is why
nothing surfaces it and a repo whose Bash fails 30% of the time looks fine.
"""

import glob
import json
import os
import time
from collections import defaultdict

import tartifacts

TAIL_BYTES = 4_000_000
WINDOWS = {"24h": 24 * 3600, "7d": 7 * 86400}
TRANSCRIPTS = os.path.expanduser("~/.claude/projects/*/*.jsonl")


def label(project):
    trimmed = project.replace("-Users-tanmaygupta-", "", 1).strip("-")
    for prefix in ("dev-quarkle-", "dev-"):
        if trimmed.startswith(prefix):
            trimmed = trimmed[len(prefix):]
    return trimmed or project


def blocks(entry):
    content = (entry.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def calls():
    """(epoch, project, tool, failed) per tool call, joined across blocks."""
    oldest = time.time() - max(WINDOWS.values())
    for path in glob.glob(TRANSCRIPTS):
        try:
            if os.path.getmtime(path) < oldest:
                continue
            project = label(os.path.basename(os.path.dirname(path)))
            size = os.path.getsize(path)
            names, stamps, failures = {}, {}, set()
            with open(path, "rb") as fh:
                if size > TAIL_BYTES:
                    fh.seek(size - TAIL_BYTES)
                    fh.readline()
                for line in fh:
                    if b"tool_use" not in line and b"tool_result" not in line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    stamp = entry.get("timestamp")
                    when = (
                        time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
                        if stamp else None
                    )
                    for block in blocks(entry):
                        if block.get("type") == "tool_use":
                            names[block.get("id")] = block.get("name") or "?"
                            stamps[block.get("id")] = when
                        elif block.get("type") == "tool_result" and block.get("is_error"):
                            failures.add(block.get("tool_use_id"))
            for call_id, name in names.items():
                if stamps.get(call_id):
                    yield stamps[call_id], project, name, call_id in failures
        except OSError:
            continue


now = time.time()
everything = list(calls())
windows = {}
for name, span in WINDOWS.items():
    tools = defaultdict(lambda: {"calls": 0, "errors": 0})
    repos = defaultdict(lambda: {"calls": 0, "errors": 0})
    for when, project, tool, failed in everything:
        if when < now - span:
            continue
        for bucket in (tools[tool], repos[project]):
            bucket["calls"] += 1
            bucket["errors"] += int(failed)
    windows[name] = {
        "tools": sorted(
            ({"tool": t, **v} for t, v in tools.items()),
            key=lambda e: -e["calls"],
        ),
        "repos": sorted(
            ({"repo": r, **v} for r, v in repos.items()),
            key=lambda e: -e["calls"],
        ),
        "calls": sum(v["calls"] for v in tools.values()),
        "errors": sum(v["errors"] for v in tools.values()),
    }

tartifacts.write_data({"at": now, "windows": windows})
day = windows["24h"]
print(f"{day['calls']} tool calls in 24h · {day['errors']} failed · {len(day['tools'])} distinct tools")
