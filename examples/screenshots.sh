#!/usr/bin/env bash
# Regenerate examples/images/*.png from the artifacts themselves.
#
# `tart render --svg` exports the frame exactly as it renders — real colours,
# real layout, no screen capture, no window management, and identical on
# every machine. rsvg-convert rasterises it because PyPI rejects SVG in a
# README (`brew install librsvg` if it's missing).
#
# Five of the eight render live personal state — task titles, hostname,
# SSID, home paths — so those are shot against the sample payloads in
# examples/demo/ instead, via `--state`. The images are then identical on
# any machine, and safe to publish. The other three show real local data
# that identifies nobody.
set -euo pipefail

cd "$(dirname "$0")/.."
OUT=examples/images
mkdir -p "$OUT"

# artifact:width:height:state
SHOTS=(
  "claude-swarm:104:20:swarm"
  "claude-burn:104:24:burn"
  "claude-pulse:104:22:"
  "claude-tools:104:26:"
  "mac-vitals:104:26:vitals"
  "mac-airwaves:104:22:airwaves"
  "mac-schedule:104:20:schedule"
  "mac-space:104:22:"
)

for shot in "${SHOTS[@]}"; do
  IFS=: read -r name width height demo <<<"$shot"
  args=(render "$name" --width "$width" --height "$height" --svg "$OUT/$name.svg")
  [ -n "$demo" ] && args+=(--state "$(cat "examples/demo/$demo.json")")
  tart "${args[@]}" >/dev/null
  rsvg-convert -z 2 "$OUT/$name.svg" -o "$OUT/$name.png"
  rm -f "$OUT/$name.svg"
  printf "  %-16s %s\n" "$name" "$(du -h "$OUT/$name.png" | cut -f1)"
done
