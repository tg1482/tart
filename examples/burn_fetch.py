"""Token burn across every Claude Code session on this machine.

Reads only the TAIL of each recently-touched transcript. The full set is
gigabytes; the last few MB of each is where today lives, and a fetch that
takes a minute is a fetch nobody runs.
"""

import datetime
import glob
import json
import os
import subprocess
import time
import urllib.request
from collections import defaultdict

import tartifacts

TAIL_BYTES = 3_000_000
WINDOW_HOURS = 24
TRANSCRIPTS = os.path.expanduser("~/.claude/projects/*/*.jsonl")

# What the /usage tab renders. Undocumented and beta-gated, so every failure
# here degrades to None rather than breaking the spend panel beside it.
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN = ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"]

# Approximate list prices, USD per million tokens. Edit to match your plan —
# this is a local estimate, not a bill.
PRICING = {
    "opus": (15.0, 75.0),
    "sonnet": (3.0, 15.0),
    "haiku": (0.80, 4.0),
}
CACHE_READ_FACTOR = 0.1
CACHE_WRITE_FACTOR = 1.25


def label(project: str) -> str:
    """`-Users-tanmaygupta-dev-quarkle-wss-service` -> `wss-service`."""
    trimmed = project.replace("-Users-tanmaygupta-", "", 1).strip("-")
    for prefix in ("dev-quarkle-", "dev-"):
        if trimmed.startswith(prefix):
            trimmed = trimmed[len(prefix):]
    return trimmed or project


def family(model: str) -> str:
    for name in PRICING:
        if name in model:
            return name
    return "other"


def cost(model: str, usage: dict) -> float:
    rate_in, rate_out = PRICING.get(family(model), (0.0, 0.0))
    fresh = usage.get("input_tokens", 0)
    read = usage.get("cache_read_input_tokens", 0)
    written = usage.get("cache_creation_input_tokens", 0)
    out = usage.get("output_tokens", 0)
    billed_in = fresh + read * CACHE_READ_FACTOR + written * CACHE_WRITE_FACTOR
    return (billed_in * rate_in + out * rate_out) / 1_000_000


def plan_usage():
    """Account-side utilization: percentages and reset times, never dollars.

    Returns None anywhere without a Keychain (the dev box), which is the
    signal for the panel to hide itself rather than render an empty gauge.
    """
    try:
        creds = subprocess.run(KEYCHAIN, capture_output=True, text=True, timeout=10)
        if creds.returncode != 0:
            return None
        oauth = json.loads(creds.stdout).get("claudeAiOauth") or {}
        request = urllib.request.Request(USAGE_URL, headers={
            "Authorization": f"Bearer {oauth.get('accessToken')}",
            "anthropic-beta": "oauth-2025-04-20",
        })
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.load(response)
    except Exception:  # keychain, network, auth, schema — all mean "no data"
        return None

    windows = []
    for key, name in (("five_hour", "5-hour session"), ("seven_day", "7-day all")):
        window = body.get(key)
        if window:
            windows.append({"name": name, "percent": window.get("utilization") or 0.0,
                            "resets_at": window.get("resets_at")})
    # per-model weekly caps arrive only in `limits`, scoped and named there
    for limit in body.get("limits") or []:
        scope = (limit.get("scope") or {}).get("model") or {}
        if limit.get("kind") == "weekly_scoped" and scope.get("display_name"):
            windows.append({"name": f"7-day {scope['display_name']}",
                            "percent": limit.get("percent") or 0.0,
                            "resets_at": limit.get("resets_at")})

    extra = body.get("extra_usage") or {}
    return {
        "plan": oauth.get("subscriptionType"),
        "windows": windows,
        "extra": {
            "enabled": extra.get("is_enabled"),
            "used": extra.get("used_credits"),
            "limit": extra.get("monthly_limit"),
            "reason": extra.get("disabled_reason"),
        } if extra else None,
    }


def records():
    """(epoch, project, model, usage) for every assistant turn in the tail."""
    cutoff = time.time() - WINDOW_HOURS * 3600
    for path in glob.glob(TRANSCRIPTS):
        try:
            if os.path.getmtime(path) < cutoff:
                continue
            project = os.path.basename(os.path.dirname(path))
            size = os.path.getsize(path)
            with open(path, "rb") as fh:
                if size > TAIL_BYTES:
                    fh.seek(size - TAIL_BYTES)
                    fh.readline()          # drop the partial line we landed in
                for line in fh:
                    if b'"usage"' not in line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    message = entry.get("message") or {}
                    usage = message.get("usage")
                    stamp = entry.get("timestamp")
                    if not usage or not stamp:
                        continue
                    # transcript stamps are UTC; mktime would read them as local
                    # and push every turn past the last hour bucket
                    epoch = datetime.datetime.fromisoformat(stamp[:19]).replace(
                        tzinfo=datetime.timezone.utc).timestamp()
                    yield epoch, project, message.get("model") or "unknown", usage
        except OSError:
            continue


now = time.time()
start = now - WINDOW_HOURS * 3600
buckets = defaultdict(lambda: {"tokens": 0, "cost": 0.0})
models = defaultdict(lambda: {"calls": 0, "input": 0, "output": 0, "cost": 0.0})
projects = defaultdict(lambda: {"tokens": 0, "cost": 0.0})
totals = {"calls": 0, "input": 0, "output": 0, "cached": 0, "cost": 0.0}

for epoch, project, model, usage in records():
    if epoch < start:
        continue
    spend = cost(model, usage)
    out = usage.get("output_tokens", 0)
    fresh = usage.get("input_tokens", 0)
    read = usage.get("cache_read_input_tokens", 0)
    written = usage.get("cache_creation_input_tokens", 0)
    moved = fresh + read + written + out

    hour = int((epoch - start) // 3600)
    buckets[hour]["tokens"] += moved
    buckets[hour]["cost"] += spend

    slot = models[family(model)]
    slot["calls"] += 1
    slot["input"] += fresh + read + written
    slot["output"] += out
    slot["cost"] += spend

    projects[project]["tokens"] += moved
    projects[project]["cost"] += spend

    totals["calls"] += 1
    totals["input"] += fresh + written
    totals["output"] += out
    totals["cached"] += read
    totals["cost"] += spend

hours = [buckets.get(h, {"tokens": 0, "cost": 0.0}) for h in range(WINDOW_HOURS)]
tartifacts.write_data({
    "at": now,
    "window_hours": WINDOW_HOURS,
    "plan_usage": plan_usage(),
    "totals": totals,
    "per_hour": {
        "tokens": [h["tokens"] for h in hours],
        "cost": [round(h["cost"], 4) for h in hours],
    },
    "models": [
        {"model": name, **slot}
        for name, slot in sorted(models.items(), key=lambda kv: -kv[1]["cost"])
    ],
    "projects": [
        {"project": label(name), **slot}
        for name, slot in sorted(projects.items(), key=lambda kv: -kv[1]["tokens"])
    ],
})
print(f"{totals['calls']} turns, ${totals['cost']:.2f} over {WINDOW_HOURS}h")
