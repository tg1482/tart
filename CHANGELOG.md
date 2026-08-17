# Changelog

## unreleased

- headless renders warn on stderr when the data is past its declared
  `stale_after` (exit still 0 — stale numbers are usable, but an agent
  reading `--json` was getting 6-hour-old data against a 1h declaration
  with nothing in the render path saying so)

## 0.3.0 — 2026-08-17

**The flight recorder.** Failure used to be silent: the background keeper
captured a fetch's output and discarded it, a broken data file was dropped
on the floor, and `render --json` printed `{"data": null}` with exit 0 — a
cron fetch once failed for 15 hours (`uv: command not found`) while the
dashboard sat frozen with the diagnosis destroyed each round.

- Every fetch — CLI, `tart run`'s pre-launch refresh, background keeper —
  records its outcome (exit code, duration, output tail) under
  `<TART_HOME>/artifacts/<name>-<hash>/`, plus an appended, capped
  `fetch.log`
- `tart logs <name>` — the last fetch's outcome and preserved output
- `tart list` shows `✗ fetch failed (exit 127, 15h ago)` distinctly from
  `⚠ data stale`
- A live artifact shows a one-line warning bar when its data file stops
  parsing or its last background fetch failed (seeded from disk, so a cron
  failure from before launch is visible on the first frame)
- `tart render` / `--json` exit non-zero when a declared data file is
  missing or unparseable, with the why on stderr (which file, the last
  fetch's outcome, and what to run next) — the frame/summary still prints
- `--state`-pinned keys are no longer clobbered by the file watcher in
  interactive mode
- `fmt.age(seconds)` — `"45s"` / `"3h"` / `"1d"`, the relative-time helper
  every artifact was hand-rolling

**Deterministic environments.** The same fetch runs in three environments
— your shell, the background keeper, cron — and only your shell has your
exports.

- `env_file` manifest key (systemd's `EnvironmentFile=`): KEY=VALUE lines
  loaded into `run`/`fetch`'s environment before spawning, overriding the
  inherited environment so all three triggers run identically. Secrets
  stay out of the manifest (committed) and the command string (`ps`). A
  declared file that can't load fails loudly and is recorded
- Every fetch records the PATH it ran under; `tart logs` shows it on
  failure — `uv: command not found` from cron is a PATH diagnosis
- `tart cron <name>` prints a crontab line with the current PATH and the
  absolute tart binary baked in, at a cadence matching `stale_after`

**Lifecycle correctness.** A process tart started never outlives tart's
interest in it, and liveness claims are checkable.

- editing an artifact's run script restarts it in place: the live loop
  watches the `.py`/`.sh` files named in `run` and re-execs on change —
  data hot-reloaded but code didn't, so a long-lived artifact ran old
  code against new data until it crashed
- fetches run in their own process group; a timeout kills the whole
  group, not just `sh` — the real fetch used to survive and rewrite the
  data file after tart reported 124
- quitting an artifact kills the keeper's in-flight fetch instead of
  orphaning it (a cancelled fetch is not recorded as a failure)
- `[live]` entries are checked against the process's actual start time
  (`ps -o etime=`), so a reused pid no longer reads as live forever
- `tart run` no longer refetches on every launch when data is present
  but `stale_after` is undeclared — the CLI twin of the keeper bug

**Checkable views and input fixes.**

- `states` manifest key + `tart render <name> --states`: declare the
  `--state` payloads your keys reach, render them all in one command —
  the crash always lived in the one view the smoke test skipped
- escape sequences are parsed to their terminator: Home/End/F-keys
  (`[1~`, `[15~`) no longer strand bytes that fired as spurious keys on
  the next press
- table cells flatten embedded newlines/tabs (a scraped title with a
  newline misaligned the whole row); `widgets.plain` documents the
  contract
- docs: use `widgets.row` for side-by-side panels, not rich's `Columns`
  (rediscovered and hand-rolled per artifact)

**A third of the memory.** `TART_PYTHON` is set for every command tart
spawns: tart's own interpreter, which has `rich`+`tartifacts` by
construction. Manifests that use it (`"run": "$TART_PYTHON show.py"`) run
as one ~20 MB process; the previous `uv run --with rich --with tartifacts`
pattern kept a ~24-34 MB uv supervisor resident beside each artifact's
Python (~60 MB each, measured) and paid resolver latency on every fetch.
`uv run` remains the right call when an artifact needs extra libraries.

## 0.1.0 — unreleased

First public release of **live terminal artifacts** — dashboards that
stay open, keep their own data fresh — or tell you they haven't — and stay findable.

- `tart run | render | list | fetch | trust | roots | reindex | --skill`
- Manifest-driven artifacts: one `.tart/<name>.json` declares the data path,
  what produces it, and when it goes stale — neither script repeats it
- Headless `--once` / `--json`, so an agent can read what it built without
  driving a terminal
- Discovery by workspace root, with a deep search that self-heals when a
  manifest moves
- `rich`-based widgets: responsive `row`, tail-trimming `trend`/`timeline`,
  `scrolling_table`, `Cursor`. Dashboards are read-only, so keys navigate
  and toggle — there is no text entry and no modal state
- A manifest does nothing until `tart trust` it, keyed by content hash, so
  a cloned repo cannot execute through a scan
- `tartifacts.write_data()` writes an artifact's data atomically

- Atomic state writes, `TART_HOME`, and exit codes automation can gate on

Published as **`tartifacts`** — the `tart` name on PyPI belongs to an
unrelated radio-telescope library, which also owns the `tart` import name.
The command you type is still `tart`.
