"""Wi-Fi, Bluetooth and the link between you and the router.

Sonoma removed the fast `airport` tool, so signal strength costs a ~5s
`system_profiler` call — which is exactly why this artifact declares
`stale_after` instead of polling: expensive truth, refreshed on a schedule.
"""

import json
import re
import subprocess
import time

import tartifacts

SAMPLES = 120


def sh(*command, timeout=15):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def wifi():
    try:
        report = json.loads(sh("system_profiler", "SPAirPortDataType", "-json"))
        face = report["SPAirPortDataType"][0]["spairport_airport_interfaces"][0]
        current = face.get("spairport_current_network_information") or {}
    except (ValueError, KeyError, IndexError):
        return {}
    noise = current.get("spairport_signal_noise") or ""
    strengths = [int(n) for n in re.findall(r"(-?\d+)\s*dBm", noise)]
    return {
        "ssid": current.get("_name"),
        "rssi": strengths[0] if strengths else None,
        "noise": strengths[1] if len(strengths) > 1 else None,
        "rate": current.get("spairport_network_rate"),
        "channel": current.get("spairport_network_channel"),
        "security": current.get("spairport_security_mode", "").replace("spairport_security_mode_", ""),
        "interface": face.get("_name"),
    }


def bluetooth():
    try:
        report = json.loads(sh("system_profiler", "SPBluetoothDataType", "-json"))
        section = report["SPBluetoothDataType"][0]
    except (ValueError, KeyError, IndexError):
        return []
    devices = []
    for entry in section.get("device_connected") or []:
        for name, info in entry.items():
            level = info.get("device_batteryLevelMain") or info.get("device_batteryLevelSingle")
            devices.append({
                "name": name,
                "kind": info.get("device_minorType") or info.get("device_majorType") or "",
                "battery": int(level.rstrip("%")) if isinstance(level, str) and level.rstrip("%").isdigit() else None,
                "left": info.get("device_batteryLevelLeft"),
                "right": info.get("device_batteryLevelRight"),
            })
    return sorted(devices, key=lambda d: (d["battery"] is None, d["battery"] or 0))


def link():
    gateway = ""
    for line in sh("netstat", "-rn").splitlines():
        if line.startswith("default"):
            gateway = line.split()[1]
            break
    result = {"gateway": gateway, "ip": sh("ipconfig", "getifaddr", "en0").strip()}
    if gateway:
        out = sh("ping", "-c", "3", "-t", "3", gateway, timeout=8)
        latency = re.search(r"= [\d.]+/([\d.]+)/", out)
        loss = re.search(r"([\d.]+)% packet loss", out)
        result["latency_ms"] = round(float(latency.group(1)), 2) if latency else None
        result["loss_percent"] = float(loss.group(1)) if loss else None
    return result


def previous():
    try:
        with open(tartifacts.data_path()) as fh:
            return json.load(fh).get("history", [])
    except (OSError, ValueError):
        return []


radio, devices, path = wifi(), bluetooth(), link()
history = previous()
history.append({
    "at": time.time(),
    "rssi": radio.get("rssi"),
    "latency": path.get("latency_ms"),
})
tartifacts.write_data({
    "at": time.time(),
    "wifi": radio,
    "link": path,
    "bluetooth": devices,
    "history": history[-SAMPLES:],
})
print(f"{radio.get('ssid') or 'no wifi'} @ {radio.get('rssi')}dBm · "
      f"{path.get('latency_ms')}ms · {len(devices)} bluetooth devices")
