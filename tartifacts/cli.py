"""The `tart` command.

    tart list                    what's declared and what's live
    tart run <name>              launch an artifact (fetches first if stale)
    tart render <name> [--json]  one frame, headless — for agents/logs
    tart fetch <name>            re-run an artifact's data-producing command
    tart logs <name>             the last fetch's outcome and output, however it was triggered
    tart register <path>         adopt a manifest living outside a scanned root
    tart trust <name>            agree to run this manifest's commands
    tart roots [add|rm <path>]   workspaces scanned for artifacts
    tart reindex                 re-find manifests too deep for the normal scan
    tart --skill                 full reference, written for an agent
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from . import (
    discover, fmt, index, manifest as manifest_mod, refresh,
    roots as roots_mod, status, trust,
)
from .manifest import Manifest

USAGE = (
    "usage: tart list | run <name> | render <name> [--json] | "
    "fetch <name> | logs <name> | register <path> | trust <name> | "
    "roots [add|rm <path>] | reindex | --skill"
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
    if ptr.fetch and ptr.is_stale() is not False:
        print(f"data stale or missing, running fetch: {ptr.fetch}", file=sys.stderr)
        _fetch(ptr, trigger="run")

    _exec(ptr, ptr.run, extra)


def _render(name: str, extra: list[str]) -> None:
    ptr = _resolve_or_exit(name)
    if not ptr.run:
        print(f'{ptr.path} has no "run" command', file=sys.stderr)
        sys.exit(1)
    mode = "--json" if "--json" in extra else "--once"
    passthrough = [a for a in extra if a != "--json"]
    _exec(ptr, ptr.run, [mode, *passthrough])


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
            scripts += named_scripts(found.root, command)
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


def _exec(found: Manifest, command: str, extra: list[str]) -> None:
    _require_trust(found)
    os.chdir(found.root)  # so relative paths resolve regardless of caller cwd
    os.environ["TART_MANIFEST"] = str(found.path.resolve())
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
    # needs to know which manifest it's serving.
    env = {**os.environ, "TART_MANIFEST": str(ptr.path.resolve())}
    before = _mtime(ptr.data_path)
    started = time.time()

    def record(**outcome) -> None:
        status.record_fetch(
            ptr.path, trigger=trigger, duration=time.time() - started, **outcome
        )

    tail: deque[str] = deque(maxlen=200)
    try:
        proc = subprocess.Popen(
            ptr.fetch, shell=True, cwd=ptr.root, env=env,
            stderr=subprocess.PIPE, text=True,
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
        proc.kill()
        proc.wait()
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
    log = status.log_tail(ptr.path)
    if log:
        print(f"\n{status.log_path(ptr.path)}:")
        print(log)


def named_scripts(root: Path, command: str) -> list[str]:
    """Local script files a command refers to — a .py/.sh token that isn't an
    option. Used by the trust refusal to show what a manifest would run."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []  # unbalanced quotes — not our job to parse shell
    return [t for t in tokens if t.endswith((".py", ".sh")) and not t.startswith("-")]


if __name__ == "__main__":
    main()
