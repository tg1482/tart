"""Every scheduled job on this machine, and whether it last worked.

`launchctl list` is the most useful opaque command on macOS: column 2 is the
LAST EXIT STATUS of a job that already ran and vanished. Nothing surfaces
it, so background jobs fail silently for months.
"""

import os
import re
import subprocess
import time

import tartifacts

SIGNALS = {1: "SIGHUP", 2: "SIGINT", 9: "SIGKILL", 15: "SIGTERM", 11: "SIGSEGV"}


def sh(*command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def explain(status):
    """launchctl reports negatives as signals and positives as exit codes."""
    if status == 0:
        return "ok", "ok"
    if status < 0:
        return "signal", f"killed by {SIGNALS.get(-status, f'signal {-status}')}"
    return "failed", f"exit {status}"


def launchd():
    jobs = []
    for line in sh("launchctl", "list").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        pid, status, label = parts[0], parts[1], parts[2].strip()
        try:
            code = int(status)
        except ValueError:
            continue
        kind, detail = explain(code)
        jobs.append({
            "source": "launchd",
            "label": label,
            "running": pid not in ("-", ""),
            "pid": None if pid == "-" else int(pid),
            "status": code,
            "kind": "running" if pid not in ("-", "") and code == 0 else kind,
            "detail": detail,
            "apple": label.startswith("com.apple."),
        })
    return jobs


def cron():
    jobs = []
    for line in sh("crontab", "-l").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" in line.split()[0]:
            continue
        fields = line.split(None, 5)
        if len(fields) < 6:
            continue
        schedule, command = " ".join(fields[:5]), fields[5]
        jobs.append({
            "source": "cron",
            "label": command,
            "schedule": schedule,
            "running": False,
            "pid": None,
            "status": 0,
            "kind": "scheduled",
            "detail": schedule,
            "apple": False,
        })
    return jobs


def user_agents():
    """Plist files you installed yourself — the ones you can actually fix."""
    found = []
    for folder in ("~/Library/LaunchAgents", "/Library/LaunchAgents", "/Library/LaunchDaemons"):
        path = os.path.expanduser(folder)
        try:
            for name in os.listdir(path):
                if name.endswith(".plist"):
                    found.append(re.sub(r"\.plist$", "", name))
        except OSError:
            continue
    return set(found)


installed = user_agents()
jobs = launchd() + cron()
for job in jobs:
    job["yours"] = job["label"] in installed or job["source"] == "cron"

rank = {"failed": 0, "signal": 1, "running": 2, "scheduled": 3, "ok": 4}
jobs.sort(key=lambda j: (not j["yours"], rank.get(j["kind"], 5), j["label"]))

broken = [j for j in jobs if j["kind"] in ("failed", "signal")]
tartifacts.write_data({
    "at": time.time(),
    "jobs": jobs,
    "counts": {
        "total": len(jobs),
        "broken": len(broken),
        "broken_yours": sum(1 for j in broken if j["yours"]),
        "running": sum(1 for j in jobs if j["running"]),
        "cron": sum(1 for j in jobs if j["source"] == "cron"),
    },
})
print(f"{len(jobs)} jobs · {len(broken)} not ok · {sum(1 for j in broken if j['yours'])} of them yours")
