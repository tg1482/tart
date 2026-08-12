"""Machine vitals, sampled into a rolling history the artifact owns.

Demonstrates the self-accumulating pattern: read the previous data file,
append this sample, keep the last N. The artifact grows its own time series
without a database — the sparklines come from nothing but repeated fetches.
"""

import json
import os
import re
import subprocess
import time

import tartifacts

SAMPLES = 120          # at 30s auto_refresh that's the last hour


def sh(*command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def battery():
    out = sh("pmset", "-g", "batt")
    percent = re.search(r"(\d+)%", out)
    state = "charging" if "AC Power" in out else "battery"
    if "charged" in out:
        state = "charged"
    left = re.search(r"(\d+:\d+) remaining", out)

    raw = sh("ioreg", "-r", "-c", "AppleSmartBattery")
    def field(name):
        found = re.search(rf'"{name}" = (\d+)', raw)
        return int(found.group(1)) if found else None

    design, full = field("DesignCapacity"), field("AppleRawMaxCapacity")
    if not full:
        full = field("MaxCapacity") if (field("MaxCapacity") or 0) > 1000 else None
    temp = field("Temperature")
    return {
        "percent": int(percent.group(1)) if percent else None,
        "state": state,
        "remaining": left.group(1) if left else None,
        "cycles": field("CycleCount"),
        "health": round(100 * full / design, 1) if full and design else None,
        "temp_c": round(temp / 100, 1) if temp else None,
    }


def memory():
    stats = sh("vm_stat")
    size = int(re.search(r"page size of (\d+)", stats).group(1))
    pages = dict(re.findall(r'"?([\w ]+)"?:\s+(\d+)\.', stats))
    def kind(name):
        return int(pages.get(name, 0)) * size
    total = int(sh("sysctl", "-n", "hw.memsize") or 0)
    used = kind("Pages active") + kind("Pages wired down") + kind("Pages occupied by compressor")
    return {"total": total, "used": used, "percent": round(100 * used / total, 1) if total else 0}


def disk():
    line = sh("df", "-k", "/System/Volumes/Data").strip().split("\n")[-1].split()
    total, used, free = int(line[1]) * 1024, int(line[2]) * 1024, int(line[3]) * 1024
    return {"total": total, "used": used, "free": free,
            "percent": round(100 * used / total, 1) if total else 0}


def processes(limit=12):
    """Top CPU consumers — the answer to "why is the fan on"."""
    out = sh("ps", "-Aceo", "pid,pcpu,pmem,comm", "-r")
    found = []
    for line in out.splitlines()[1:]:
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            found.append({"pid": int(parts[0]), "cpu": float(parts[1]),
                          "mem": float(parts[2]), "name": parts[3].strip()})
        except ValueError:
            continue
    return found[:limit]


def previous():
    """The history we've already collected, if any."""
    try:
        with open(tartifacts.data_path()) as fh:
            return json.load(fh).get("history", [])
    except (OSError, ValueError):
        return []


cores = int(sh("sysctl", "-n", "hw.ncpu") or 1)
load = [float(n) for n in (sh("sysctl", "-n", "vm.loadavg").strip("{} \n").split() or [0, 0, 0])]
power, ram, storage = battery(), memory(), disk()

history = previous()
history.append({
    "at": time.time(),
    "load": round(100 * load[0] / cores, 1),
    "memory": ram["percent"],
    "battery": power["percent"],
})
history = history[-SAMPLES:]

tartifacts.write_data({
    "at": time.time(),
    "host": os.uname().nodename.replace(".local", ""),
    "uptime": sh("uptime").split("up ")[-1].split(", load")[0].strip() if sh("uptime") else "?",
    "cores": cores,
    "load": load,
    "battery": power,
    "memory": ram,
    "disk": storage,
    "history": history,
    "processes": processes(),
})
print(f"{power['percent']}% battery · load {load[0]} · mem {ram['percent']}% · {len(history)} samples")
