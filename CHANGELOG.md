# Changelog

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
