"""Where the disk went, and how much of it you could have back.

Deliberately the slow one (~20s of `du`), which is why its manifest declares
`stale_after: 6h` and no `auto_refresh`. Expensive truth belongs on a
schedule — run it from cron and the dashboard is always warm.
"""

import os
import subprocess
import time

import tartifacts

# (label, path, reclaimable) — reclaimable means "deleting this is safe and
# the tool that made it will rebuild it".
CANDIDATES = [
    ("User caches", "~/Library/Caches", True),
    ("Shell/tool cache", "~/.cache", True),
    ("npm cache", "~/.npm", True),
    ("Xcode DerivedData", "~/Library/Developer/Xcode/DerivedData", True),
    ("Xcode device support", "~/Library/Developer/Xcode/iOS DeviceSupport", True),
    ("Homebrew cache", "~/Library/Caches/Homebrew", True),
    ("uv cache", "~/.cache/uv", True),
    ("Trash", "~/.Trash", True),
    ("Docker disk image", "~/Library/Containers/com.docker.docker/Data/vms/0/data", False),
    ("Containers", "~/Library/Containers", False),
    ("Application Support", "~/Library/Application Support", False),
    ("Downloads", "~/Downloads", False),
]


def sh(*command, timeout=60):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def size_of(path):
    """Bytes actually on disk. `du` is sparse-file aware; `ls` is not, which
    is why Docker.raw looks like 460G and occupies 62G."""
    out = sh("du", "-sk", os.path.expanduser(path))
    try:
        return int(out.split()[0]) * 1024
    except (IndexError, ValueError):
        return None


def volumes():
    found = []
    for line in sh("df", "-k").splitlines()[1:]:
        parts = line.split()
        if len(parts) < 9 or not parts[0].startswith("/dev/"):
            continue
        mount = " ".join(parts[8:])
        if mount.startswith("/System/Volumes/") and mount != "/System/Volumes/Data":
            continue
        total, used, free = int(parts[1]) * 1024, int(parts[2]) * 1024, int(parts[3]) * 1024
        found.append({
            "mount": mount, "total": total, "used": used, "free": free,
            "percent": round(100 * used / total, 1) if total else 0,
        })
    return found


entries = []
for label, path, reclaimable in CANDIDATES:
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        continue
    size = size_of(path)
    if size:
        entries.append({"label": label, "path": path, "bytes": size, "reclaimable": reclaimable})
entries.sort(key=lambda e: -e["bytes"])

# ~/.cache/uv lives inside ~/.cache, so summing both counts those bytes
# twice. Mark the nested ones and leave them out of the headline — they are
# still worth showing, as the breakdown of the parent.
for entry in entries:
    mine = os.path.expanduser(entry["path"]).rstrip("/") + "/"
    entry["nested"] = any(
        mine.startswith(os.path.expanduser(other["path"]).rstrip("/") + "/")
        for other in entries
        if other is not entry
    )

snapshots = [
    line.strip() for line in sh("tmutil", "listlocalsnapshots", "/").splitlines()
    if line.strip().startswith("com.apple")
]

tartifacts.write_data({
    "at": time.time(),
    "volumes": volumes(),
    "entries": entries,
    "reclaimable": sum(e["bytes"] for e in entries if e["reclaimable"] and not e["nested"]),
    "snapshots": len(snapshots),
})
gb = sum(e["bytes"] for e in entries if e["reclaimable"] and not e["nested"]) / 1024**3
print(f"{len(entries)} locations · {gb:.1f} GB reclaimable · {len(snapshots)} local snapshots")
