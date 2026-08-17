"""The `tart` command.

    tart list                    what's declared and what's live
    tart run <name>              launch an artifact (fetches first if stale)
    tart render <name> [--json]  one frame, headless — for agents/logs
    tart render <name> --states  smoke every declared state, not just the base frame
    tart fetch <name>            re-run an artifact's data-producing command
    tart logs <name>             the last fetch's outcome and output, however it was triggered
    tart cron <name>             a crontab line that keeps its data fresh, PATH included
    tart cron --sync             install/refresh a managed crontab block for every auto-refresh artifact
    tart restart <name> | --all  re-exec live artifacts in place — same pane, new code
    tart register <path>         adopt a manifest living outside a scanned root
    tart trust <name>            agree to run this manifest's commands
    tart roots [add|rm <path>]   workspaces scanned for artifacts
    tart reindex                 re-find manifests too deep for the normal scan
    tart --skill                 full reference, written for an agent
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import zlib
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from . import (
    discover, envfile, fmt, index, manifest as manifest_mod, refresh,
    registry, roots as roots_mod, status, trust,
)
from .manifest import Manifest

USAGE = (
    "usage: tart list | run <name> | render <name> [--json] | "
    "fetch <name> | logs <name> | cron <name>|--sync | restart <name>|--all | "
    "register <path> | trust <name> | roots [add|rm <path>] | reindex | --skill"
)

SKILL_PATH = Path(__file__).with_name("skill.md")


HELP = {"--help", "-h", "help"}


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else None

    # `--help` is help only as the command itself. In `tart run x --help` the
    # `--help` belongs to the artifact and passes through, rather than tart
    # hijacking a flag its own subcommand doesn't own.
    if cmd in HELP:
        print(__doc__.strip())
        return
    if cmd in ("--skill", "skill"):
        print(SKILL_PATH.read_text(), end="")
    elif cmd == "list":
        sys.exit(1 if discover.print_listing() else 0)
    elif cmd == "run" and len(args) >= 2:
        _run(args[1], args[2:])
    elif cmd == "render" and len(args) >= 2:
        _render(args[1], args[2:])
    elif cmd == "fetch" and len(args) >= 2:
        _fetch_cmd(args[1])
    elif cmd == "logs" and len(args) >= 2:
        _logs(args[1])
    elif cmd == "cron" and len(args) >= 2:
        _cron(args[1:])
    elif cmd == "restart":
        _restart(args[1:])
    elif cmd == "register" and len(args) >= 2:
        _register(args[1])
    elif cmd == "trust":
        _trust(args[1:])
    elif cmd == "roots":
        _roots(args[1:])
    elif cmd == "reindex":
        found = index.reindex()
        print(f"indexed {len(found)} manifest{'s' if len(found) != 1 else ''}")
        for path in found:
            print(f"  {path}")
    else:
        print(USAGE, file=sys.stderr)
        sys.exit(1)


def _resolve_or_exit(name: str) -> Manifest:
    matches = discover.ambiguous(name)
    if len(matches) > 1:
        print(f"'{name}' is declared in {len(matches)} places. Qualify it:", file=sys.stderr)
        for ptr in matches:
            print(f"  tart run {discover.qualified(ptr)}", file=sys.stderr)
        sys.exit(1)
    ptr = discover.resolve(name)
    if ptr is None:
        print(f"no artifact '{name}' found.", file=sys.stderr)
        print("  declared artifacts:  tart list", file=sys.stderr)
        print("  add a workspace:    tart roots add <path>", file=sys.stderr)
        sys.exit(1)
    return ptr


def _roots(args: list[str]) -> None:
    if args and args[0] == "add" and len(args) >= 2:
        added = roots_mod.add(args[1])
        print(f"added root {added}")
    elif args and args[0] in ("rm", "remove") and len(args) >= 2:
        roots_mod.remove(args[1])
        print(f"removed root {args[1]}")
    current = roots_mod.load()
    if not current:
        print("no roots configured; only ./.tart/ and ~/.tart/ are scanned")
        return
    print("scanning:")
    for root in current:
        print(f"  {root}")


def _run(name: str, extra: list[str]) -> None:
    ptr = _resolve_or_exit(name)
    if not ptr.run:
        print(f'{ptr.path} has no "run" command', file=sys.stderr)
        sys.exit(1)

    # Refresh first when the data is declared-stale (or absent) and we know
    # how to produce it — otherwise the artifact opens showing numbers its own
    # manifest says not to trust.
    if ptr.fetch and _needs_fetch(ptr):
        print(f"data stale or missing, running fetch: {ptr.fetch}", file=sys.stderr)
        _fetch(ptr, trigger="run")

    _exec(ptr, ptr.run, extra)


def _render(name: str, extra: list[str]) -> None:
    ptr = _resolve_or_exit(name)
    if not ptr.run:
        print(f'{ptr.path} has no "run" command', file=sys.stderr)
        sys.exit(1)
    if "--states" in extra:
        _render_states(ptr)
        return
    mode = "--json" if "--json" in extra else "--once"
    passthrough = [a for a in extra if a != "--json"]
    _exec(ptr, ptr.run, [mode, *passthrough])


def _render_states(ptr: Manifest) -> None:
    """One frame per declared state, plus the base frame — the smoke matrix.

    `render --once` proves the initial view; every other view sits behind a
    keypress, and the crash always lives in the one view nobody rendered
    (a KeyError in a `rows()` only called on 'd' survived every headless
    check and hit the user live). Declaring the states next to `run` makes
    "check them all" one command instead of a hand-maintained loop."""
    _require_trust(ptr)
    bad = [s for s in ptr.states if not isinstance(s, dict)]
    if bad:
        print(f'"states" entries must be JSON objects, got: {bad[0]!r}', file=sys.stderr)
        sys.exit(1)
    env = {**os.environ, **_env_overlay(ptr),
           "TART_MANIFEST": str(ptr.path.resolve()), "TART_PYTHON": sys.executable}
    frames = [("(base)", None)] + [(json.dumps(s), s) for s in ptr.states]
    failures = 0
    for label, state in frames:
        parts = [ptr.run, "--once"]
        if state is not None:
            parts += ["--state", shlex.quote(json.dumps(state))]
        proc = subprocess.run(
            " ".join(parts), shell=True, cwd=ptr.root, env=env,
            capture_output=True, text=True, timeout=refresh.FETCH_TIMEOUT,
        )
        if proc.returncode == 0:
            print(f"ok    {label}")
        else:
            failures += 1
            print(f"FAIL  {label}  (exit {proc.returncode})")
            for line in proc.stderr.strip().splitlines()[-6:]:
                print(f"      {line}")
    if failures:
        print(f"\n{failures} of {len(frames)} states failed", file=sys.stderr)
        sys.exit(1)
    if not ptr.states:
        print('\nonly the base frame — declare "states": [{...}] in the manifest')
        print("to smoke the views your keys reach")


def _require_trust(found: Manifest) -> None:
    """The gate. Everything that runs a manifest's command comes through
    here, so there is exactly one place to audit."""
    if trust.is_trusted(found.path):
        return
    known = trust.trusted_paths()
    changed = str(found.path.resolve()) in known
    print(
        f"{found.path} is not trusted." if not changed
        else f"{found.path} has changed since you trusted it.",
        file=sys.stderr,
    )
    print("\nIt would run:", file=sys.stderr)
    scripts = []
    for label, command in (("run", found.run), ("fetch", found.fetch)):
        if command:
            print(f"  {label:<6} {command}", file=sys.stderr)
            scripts += manifest_mod.named_scripts(command)
    if scripts:
        print("\nWhich is really these files. Read them, not just the manifest:",
              file=sys.stderr)
        for script in dict.fromkeys(scripts):
            print(f"  {found.root / script}", file=sys.stderr)
    print(
        f"\nThen: tart trust {found.path.stem}"
        "\n\nTrust covers this manifest's contents only. Those scripts can"
        "\nchange afterwards without asking again — as with direnv and .envrc."
        "\nThe gate exists because tart finds manifests by scanning your roots,"
        "\nso a repo you cloned can offer one to `tart run`.",
        file=sys.stderr,
    )
    sys.exit(1)


def _register(where: str) -> None:
    """Adopt a manifest by path, wherever it lives.

    Records the LOCATION rather than copying the file: a manifest names
    paths inside its own repo, so a copy in ~/.tart would rot the moment
    the repo moved — the reason `systemctl enable` symlinks too.

    Registering also trusts it. The gate exists because tart *discovers*
    manifests by scanning, so a repo you cloned can offer one you never
    chose; naming a file on the command line IS that choice. Editing it
    still asks again, because trust is keyed by content hash.
    """
    path = Path(os.path.expanduser(where)).resolve()
    found = manifest_mod.load(path)
    if found is None:
        print(f"{path}: {manifest_mod.problem(path) or 'not a usable manifest'}", file=sys.stderr)
        sys.exit(1)

    index.remember(path)
    trust.trust(path)
    print(f"registered {found.title}")
    print(f"  {path}")
    for label, command in (("run", found.run), ("fetch", found.fetch)):
        if command:
            print(f"  {label:<6} {command}")
    print("\ntrusted, because you named it. Editing it asks again.")
    print(f"  tart run {path.stem}")


def _trust(args: list[str]) -> None:
    if args and args[0] == "--list":
        known = trust.trusted_paths()
        if not known:
            print("no trusted manifests")
            return
        for path in known:
            state = "ok" if trust.is_trusted(Path(path)) else "changed — will not run"
            print(f"  {path}  ({state})")
        return
    if args and args[0] == "--all":
        # The migration path, run once and explicitly: everything already
        # declared on this machine was put there by you.
        for found in discover.declared():
            trust.trust(found.path)
            print(f"  trusted {found.path}")
        return
    if not args:
        print("usage: tart trust <name> | --all | --list | --forget <name>", file=sys.stderr)
        sys.exit(1)
    if args[0] == "--forget" and len(args) >= 2:
        found = _resolve_or_exit(args[1])
        was_known = str(found.path.resolve()) in trust.trusted_paths()
        trust.forget(found.path)
        print(f"forgot {args[1]}" if was_known else f"{args[1]} was not trusted")
        return
    found = _resolve_or_exit(args[0])
    trust.trust(found.path)
    print(f"trusted {found.path}")


def _env_overlay(ptr: Manifest) -> dict[str, str]:
    """The declared env_file's values, or a loud exit when it can't be
    loaded. Loud because the downstream failure — an API rejecting a blank
    key — points anywhere but at the missing file."""
    if ptr.env_file_path is None:
        return {}
    try:
        return envfile.load(ptr.env_file_path)
    except OSError as bad:
        print(f"env_file {ptr.env_file_path} cannot be loaded: {bad}", file=sys.stderr)
        sys.exit(1)


def _exec(found: Manifest, command: str, extra: list[str]) -> None:
    _require_trust(found)
    overlay = _env_overlay(found)
    os.chdir(found.root)  # so relative paths resolve regardless of caller cwd
    os.environ.update(overlay)
    os.environ["TART_MANIFEST"] = str(found.path.resolve())
    os.environ["TART_PYTHON"] = sys.executable
    full = " ".join([command, *(shlex.quote(a) for a in extra)])
    # exec, not subprocess: the artifact replaces this process, so herdr/tmux
    # see it directly in the pane and Ctrl-C reaches it with no wrapper.
    os.execvp("sh", ["sh", "-c", full])


def _fetch_cmd(name: str) -> None:
    ptr = _resolve_or_exit(name)
    if not ptr.fetch:
        print(f'{ptr.path} has no "fetch" command', file=sys.stderr)
        sys.exit(1)
    sys.exit(_fetch(ptr))


def _needs_fetch(ptr: Manifest) -> bool:
    """Missing data is worth fetching, declared-stale data is worth
    fetching. Present data with no `stale_after` is NOT: is_stale() is None
    there, and treating unjudgeable as "fetch" re-ran a potentially
    expensive command on every single launch — the same bug the keeper's
    should_fetch() already fixed for the background path."""
    if ptr.data_path is None:
        return True  # no file to judge by; the declared fetch is all we have
    if not ptr.data_path.exists():
        return True
    return ptr.is_stale() is True


def _mtime(path: Path | None) -> float | None:
    try:
        return path.stat().st_mtime if path else None
    except OSError:
        return None


def _fetch(ptr: Manifest, trigger: str = "cli") -> int:
    """Runs the fetch with stderr both shown live AND recorded — the record
    is what `tart list`, the warning bar and `tart logs` read later, since a
    diagnosis printed once to a terminal (or to cron's mail) is gone."""
    _require_trust(ptr)
    # The fetch script asks the manifest where to write (data_path()), so it
    # needs to know which manifest it's serving. TART_MANIFEST goes on last:
    # it is tart's own contract, not the env_file's to override.
    env = dict(os.environ)
    before = _mtime(ptr.data_path)
    started = time.time()

    def record(**outcome) -> None:
        status.record_fetch(
            ptr.path, trigger=trigger, duration=time.time() - started,
            path=env.get("PATH"), **outcome,
        )

    if ptr.env_file_path is not None:
        try:
            env.update(envfile.load(ptr.env_file_path))
        except OSError as bad:
            record(error=f"env_file {ptr.env_file_path} cannot be loaded: {bad}")
            print(f"env_file {ptr.env_file_path} cannot be loaded: {bad}", file=sys.stderr)
            return 1
    env["TART_MANIFEST"] = str(ptr.path.resolve())
    env["TART_PYTHON"] = sys.executable

    tail: deque[str] = deque(maxlen=200)
    try:
        # start_new_session: the command is a shell line, so killing on
        # timeout must reach the whole process GROUP. Killing only `sh`
        # left the real fetch (`uv run python snapshot.py`, `curl`) alive —
        # possibly rewriting the data file minutes after tart reported 124.
        proc = subprocess.Popen(
            ptr.fetch, shell=True, cwd=ptr.root, env=env,
            stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
    except OSError as bad:
        record(error=str(bad))
        print(f"fetch could not start: {bad}", file=sys.stderr)
        return 1
    # Tee rather than capture: a human watching a 2-minute fetch still sees
    # its progress, while the same lines land in the record.
    pump = threading.Thread(target=_tee, args=(proc.stderr, tail), daemon=True)
    pump.start()
    try:
        proc.wait(timeout=refresh.FETCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        # Bounded like the background keeper's, so a wedged fetch can't hang
        # `tart run` before the artifact ever draws.
        refresh.kill_group(proc)
        record(error=f"timed out after {refresh.FETCH_TIMEOUT:.0f}s", output="".join(tail))
        print(f"fetch timed out after {refresh.FETCH_TIMEOUT:.0f}s", file=sys.stderr)
        return 124  # the conventional timeout status
    pump.join(timeout=5)

    if proc.returncode < 0:
        # Killed by a signal: subprocess reports -N. `sys.exit(-N)` truncates
        # to a meaningless status; the convention cron and CI expect is 128+N.
        signum = -proc.returncode
        record(error=f"killed by signal {signum}", output="".join(tail))
        print(f"fetch killed by signal {signum}", file=sys.stderr)
        return 128 + signum
    if proc.returncode != 0:
        record(exit_code=proc.returncode, output="".join(tail))
        print(f"fetch failed (exit {proc.returncode})", file=sys.stderr)
        return proc.returncode

    # Verify the fix we prescribe. A fetch that writes somewhere the manifest
    # doesn't declare exits 0 saying "written", while the data file it was
    # meant to produce never appears — a silent no-op. Checked by
    # mtime, not existence: a stale file left from a previous run passed the
    # existence check and let the loop survive every fetch after the first.
    if ptr.data:
        after = _mtime(ptr.data_path)
        problem = (
            "still does not exist" if after is None
            else "was not rewritten" if before is not None and after <= before
            else None
        )
        if problem:
            record(error=f"exited 0 but {ptr.data_path} {problem}", output="".join(tail))
            print(
                f"fetch exited 0 but {ptr.data_path} {problem}.\n"
                f"  the fetch command likely writes elsewhere — it should write to\n"
                f"  tartifacts.data_path(), which resolves to the manifest's \"data\".",
                file=sys.stderr,
            )
            return 1
    record(exit_code=0, output="".join(tail))
    return proc.returncode


def _tee(pipe, tail: deque) -> None:
    for line in pipe:
        sys.stderr.write(line)
        tail.append(line)


def _restart(args: list[str]) -> None:
    """Re-exec live artifacts in place — same pane, new code.

    The recurring hazard: a pane launched days ago keeps running old code
    (tart's AND its own pre-edit scripts) while everything around it moves
    on — one ran a silently-failing keeper for four days. The artifact
    installs a SIGUSR1 handler that triggers the same re-exec as the
    code-change watcher; this sends it. Artifacts launched before the
    handler existed would DIE on SIGUSR1 (the default action), so those
    get named instead of signalled."""
    if not args:
        print("usage: tart restart <name> | --all", file=sys.stderr)
        sys.exit(1)
    entries = registry.live()
    if args[0] != "--all":
        target = _resolve_or_exit(args[0]).path.resolve()
        entries = [e for e in entries if Path(e.manifest).resolve() == target]
        if not entries:
            print(f"'{args[0]}' is not running — tart run {args[0]}", file=sys.stderr)
            sys.exit(1)
    if not entries:
        print("nothing is live")
        return
    for entry in entries:
        if not entry.restartable:
            print(f"{entry.title} ({entry.where}): launched before restart support — "
                  f"q it and `tart run` again")
            continue
        try:
            os.kill(entry.pid, signal.SIGUSR1)
            print(f"restarting {entry.title} ({entry.where})")
        except OSError as bad:
            print(f"{entry.title} (pid {entry.pid}): {bad}", file=sys.stderr)


CRON_BEGIN = "# >>> tart cron --sync — managed block, edits inside are overwritten"
CRON_END = "# <<< tart cron --sync"
# Below this, cron adds nothing over the in-artifact keeper (cron can't go
# sub-minute anyway) and would hammer a fetch that only matters live.
CRON_MIN_STALE = 600.0


def _cron(args: list[str]) -> None:
    """Register a standing fetch in the crontab's managed block. `--show`
    prints the line without installing; `--sync` refreshes the whole block.

    Registration is the default because this command's purpose is that the
    data stays fresh — the print-only version shipped first, and its line
    predictably never made it into anyone's crontab: an API that advises
    instead of acting leaves the loop open.

    Cron's environment is almost empty — no /opt/homebrew/bin, no ~/.local
    — so the line that works pasted into a shell fails under cron with
    `command not found`, silently, all night. Baking the *current* PATH and
    the absolute tart binary into the line removes the whole failure class;
    the flight recorder catches whatever remains.
    """
    if args[0] == "--sync":
        _cron_sync()
        return
    name = args[0]
    ptr = _resolve_or_exit(name)
    if not ptr.fetch:
        print(f'{ptr.path} has no "fetch" command — nothing for cron to run', file=sys.stderr)
        sys.exit(1)
    if ptr.stale_after is None:
        print("# no stale_after declared — hourly is a guess; adjust freely", file=sys.stderr)
    if "--show" in args:
        print(_cron_line(name, ptr))
        print(
            f"\ninstall it with: tart cron {name}"
            f"\ncheck on it with: tart logs {name}   (every fetch records its outcome)",
            file=sys.stderr,
        )
        return
    _cron_sync(extra={name})


def _cron_line(name: str, ptr: Manifest) -> str:
    which = shutil.which("tart")
    binary = (
        shlex.quote(which) if which
        else f"{shlex.quote(sys.executable)} -m tartifacts.cli"
    )
    line = (
        f"{_cron_schedule(ptr.stale_after, name)} "
        f"PATH={shlex.quote(os.environ.get('PATH', '/usr/bin:/bin'))} "
        f"{binary} fetch {shlex.quote(name)}"
    )
    # cron treats an unescaped % as end-of-command (the rest becomes stdin)
    # — a % anywhere in PATH or a name would silently truncate the command.
    return line.replace("%", "\\%")


def _cron_schedule(stale_after: float | None, name: str = "") -> str:
    """A cadence matching the declared staleness limit, so the data is
    never much older than the manifest says to trust. The minute offset is
    hashed from the name so ten hourly artifacts don't all fire at :00."""
    offset = zlib.crc32(name.encode()) % 60
    if stale_after is None:
        return f"{offset} * * * *"
    if stale_after < 3600:
        return f"*/{max(5, int(stale_after // 60))} * * * *"
    if stale_after < 86400:
        return f"{offset} */{int(stale_after // 3600)} * * *"
    return f"{offset} 9 * * *"


def _cron_sync(extra: set[str] = frozenset()) -> None:
    """One managed crontab block covering every artifact registered to
    stay fresh — the "for good" half of auto_refresh. The keeper only
    exists while a pane is open, so freshness used to be a side effect of
    somebody's terminal layout; this makes it a machine-level standing
    order, using the daemon the OS already runs.

    Membership is a union: manifests declaring `auto_refresh`, names
    already in the block (a `tart cron <name>` registration survives every
    later --sync), and `extra` (the name being registered right now).
    Explicitly registered names skip the sub-10m floor — typing the
    command is the opt-in the floor exists to demand."""
    existing = _read_crontab()
    damage = _marker_problem(existing)
    if damage:
        # Never guess at a damaged block: a missing END marker would make
        # everything below BEGIN look managed — and get deleted.
        print(f"refusing to touch the crontab: {damage}", file=sys.stderr)
        print("fix the markers with crontab -e, then re-run", file=sys.stderr)
        sys.exit(1)
    unmanaged = _strip_managed(existing)
    explicit = _managed_names(existing) | set(extra)
    lines, skipped = [], []
    for found in discover.declared():
        name = found.path.stem
        if not (name in explicit or (found.fetch and found.auto_refresh)):
            continue
        if not found.fetch:
            skipped.append((name, 'no "fetch" command'))
            continue
        if not trust.is_trusted(found.path):
            skipped.append((name, "not trusted — tart trust it first"))
            continue
        if (name not in explicit
                and found.stale_after is not None and found.stale_after < CRON_MIN_STALE):
            skipped.append((name, f"stale_after under {fmt.age(CRON_MIN_STALE)} — "
                                  f"keeper territory (tart cron {name} to force)"))
            continue
        if f"tart fetch {name}" in unmanaged:
            skipped.append((name, "an unmanaged crontab line already fetches it — left alone"))
            continue
        lines.append(_cron_line(name, found))
    updated = _with_managed_block(existing, lines)
    if updated != existing:
        _write_crontab(updated)
    for line in lines:
        print(f"installed: {line.split(' PATH=')[0]} … tart fetch {shlex.split(line)[-1]}")
    for name, why in skipped:
        print(f"skipped:   {name} — {why}")
    if not lines and not skipped:
        print("no artifact declares auto_refresh + fetch; nothing to install")


def _read_crontab() -> str:
    override = os.environ.get("TART_CRONTAB")  # a file standing in for the
    if override:                               # real crontab — the seam that
        try:                                   # lets tests exercise real
            return Path(override).read_text()  # registration without touching
        except OSError:                        # the developer's machine
            return ""
    try:
        proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    except OSError:
        return ""  # no crontab binary; the write will fail loudly before harm
    if proc.returncode == 0:
        return proc.stdout
    if "no crontab" in proc.stderr.lower():
        return ""  # genuinely empty
    # Any OTHER failure must abort: treating a transient error as "empty"
    # would make the next write replace the user's whole crontab with just
    # the managed block.
    print(f"crontab -l failed: {proc.stderr.strip()}", file=sys.stderr)
    sys.exit(1)


def _write_crontab(text: str) -> None:
    override = os.environ.get("TART_CRONTAB")
    if override:
        Path(override).write_text(text)
        return
    proc = subprocess.run(["crontab", "-"], input=text, text=True, capture_output=True)
    if proc.returncode != 0:
        print(f"crontab refused the update: {proc.stderr.strip()}", file=sys.stderr)
        sys.exit(1)


def _marker_problem(text: str) -> str | None:
    """Why the managed block's markers can't be trusted, or None. Exactly
    zero or one of each, BEGIN before END — anything else means a human
    edited the markers, and guessing at the boundary risks eating their
    lines."""
    lines = [line.strip() for line in text.splitlines()]
    begins = [i for i, line in enumerate(lines) if line == CRON_BEGIN]
    ends = [i for i, line in enumerate(lines) if line == CRON_END]
    if len(begins) > 1 or len(ends) > 1:
        return "duplicate managed-block markers"
    if len(begins) != len(ends):
        return "unbalanced managed-block markers (one of begin/end is missing)"
    if begins and begins[0] > ends[0]:
        return "managed-block end marker appears before the begin marker"
    return None


def _managed_names(text: str) -> set[str]:
    """Artifact names already registered in the managed block — the last
    token of each `... tart fetch <name>` line."""
    names = set()
    inside = False
    for line in text.splitlines():
        if line.strip() == CRON_BEGIN:
            inside = True
        elif line.strip() == CRON_END:
            inside = False
        elif inside and line.strip():
            try:
                names.add(shlex.split(line)[-1])
            except ValueError:
                continue
    return names


def _strip_managed(text: str) -> str:
    """The crontab without our managed block — what --sync must preserve
    byte for byte. Everything outside the markers belongs to the user."""
    out, inside = [], False
    for line in text.splitlines():
        if line.strip() == CRON_BEGIN:
            inside = True
            continue
        if line.strip() == CRON_END:
            inside = False
            continue
        if not inside:
            out.append(line)
    return "\n".join(out)


def _with_managed_block(text: str, lines: list[str]) -> str:
    kept = _strip_managed(text).rstrip("\n")
    if not lines:
        return kept + "\n" if kept else ""
    block = "\n".join([CRON_BEGIN, *lines, CRON_END])
    return (kept + "\n\n" if kept else "") + block + "\n"


def _logs(name: str) -> None:
    ptr = _resolve_or_exit(name)
    last = status.last_fetch(ptr.path)
    if last is None:
        print(f"no fetch recorded for {name} — nothing has run its fetch yet")
        return
    verdict = "ok" if last["ok"] else f"FAILED ({status.describe(last)})"
    print(
        f"last fetch: {verdict}, {fmt.age(time.time() - last['at'])} ago "
        f"(trigger: {last['trigger']}, took {last['duration']}s)"
    )
    broken = status.failing_for(last)
    if broken is not None:
        print(f"failing for {fmt.age(broken)} — last success "
              f"{fmt.age(time.time() - last['last_ok'])} ago")
    if not last["ok"] and last.get("path"):
        # PATH is the variable that actually differs between a shell, the
        # keeper and cron — `uv: command not found` is a PATH diagnosis.
        print(f"ran with PATH={last['path']}")
    log = status.log_tail(ptr.path)
    if log:
        print(f"\n{status.log_path(ptr.path)}:")
        print(log)




if __name__ == "__main__":
    main()
