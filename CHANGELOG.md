# Changelog

## 0.3 — unreleased

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
