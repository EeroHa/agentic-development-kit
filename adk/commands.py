"""Commands Eero runs to adjust the wall: allow-dir, never-dir, reopen, integration, status."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from adk import state
from adk.cli import register
from adk.jsonio import InvalidJson
from adk.paths import Paths, resolve
from adk.wall.generate import load_policy


def _hidden_options(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--home", help=argparse.SUPPRESS)
    sub.add_argument("--root", help=argparse.SUPPRESS)


def _paths(args: argparse.Namespace) -> Paths:
    return resolve(
        home=Path(args.home) if args.home else None,
        root=Path(args.root) if args.root else Path("/"),
    )


def _err(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 1


def _guarded(run):
    def wrapper(args: argparse.Namespace) -> int:
        try:
            return run(args)
        except InvalidJson as e:
            return _err(str(e))
    return wrapper


def _dir_build(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("dir", metavar="DIR")
    sub.add_argument("--remove", action="store_true")
    _hidden_options(sub)


# allow-dir

def _allow_run(args: argparse.Namespace) -> int:
    p = _paths(args)
    d = Path(args.dir).expanduser().resolve()
    if args.remove:
        if state.remove_allowed_dir(p, d):
            print(f"no longer allowed {d} (updated {p.claude_user_settings})")
        else:
            print(f"was not allowed: {d}")
        return 0
    if not d.is_dir():
        return _err(f"not a directory: {d}")
    if state.add_allowed_dir(p, d):
        print(f"allowed {d} (updated {p.claude_user_settings})")
    else:
        print(f"already allowed: {d}")
    return 0


# never-dir

def _never_run(args: argparse.Namespace) -> int:
    p = _paths(args)
    d = Path(args.dir).expanduser().resolve()
    if args.remove:
        if state.remove_never_dir(p, d):
            print(f"no longer never-allowed {d} (updated {p.lists_file})")
        else:
            print(f"was not on the never list: {d}")
        return 0
    if state.add_never_dir(p, d):
        print(f"never allowed {d} (updated {p.lists_file})")
    else:
        print(f"already never-allowed: {d}")
    return 0


# reopen

def _reopen_build(sub: argparse.ArgumentParser) -> None:
    _dir_build(sub)
    sub.add_argument("--write", action="store_true", help="also allow writing")


def _secret_conflict(p: Paths, d: Path) -> str | None:
    home = p.home.resolve()
    if d == home:
        return "refusing: that is the home directory itself"
    for entry in load_policy()["secret_dirs"]:
        if entry.startswith("~/"):
            secret = (p.home / entry[2:]).resolve()
        else:
            secret = Path(entry).resolve()
        if d == secret or secret in d.parents or d in secret.parents:
            return f"refusing: {d} overlaps the secret location {secret}"
    return None


def _reopen_run(args: argparse.Namespace) -> int:
    p = _paths(args)
    d = Path(args.dir).expanduser().resolve()
    if args.remove:
        if state.remove_reopened(p, d):
            print(f"closed again {d} (updated {p.claude_user_settings})")
        else:
            print(f"was not re-opened: {d}")
        return 0
    if not d.exists():
        return _err(f"does not exist: {d}")
    conflict = _secret_conflict(p, d)
    if conflict:
        return _err(conflict)
    if state.add_reopened(p, d, write=args.write):
        mode = "read and write" if args.write else "read"
        print(f"re-opened {d} for {mode} (updated {p.claude_user_settings})")
    else:
        print(f"already re-opened: {d}")
    return 0


# integration

def _integration_build(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("action", choices=["on", "off"])
    sub.add_argument("name", choices=sorted(state.INTEGRATIONS))
    sub.add_argument("--project", metavar="DIR")
    _hidden_options(sub)


def _integration_run(args: argparse.Namespace) -> int:
    p = _paths(args)
    project = Path(args.project).expanduser().resolve() if args.project else Path.cwd().resolve()
    on = args.action == "on"
    if state.set_integration(p, project, args.name, on):
        print(f"{args.name} switched {args.action} for {project} (updated {p.lists_file})")
    else:
        print(f"{args.name} already {args.action} for {project}")
    return 0


# status

def _status_build(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--project", metavar="DIR")
    _hidden_options(sub)


def _section(title: str, items: list[str]) -> None:
    print(f"{title}:")
    for item in items or ["(none)"]:
        print(f"  {item}")


def _status_run(args: argparse.Namespace) -> int:
    p = _paths(args)
    project = Path(args.project).expanduser().resolve() if args.project else Path.cwd().resolve()
    ro = state.reopened(p)
    reopened_lines = [f"{x} (read)" for x in ro["read"]] + [f"{x} (write)" for x in ro["write"]]
    _section("Allowed folders", [str(x) for x in state.allowed_dirs(p)])
    _section("Never-allowed folders", state.load_lists(p)["never_dirs"])
    _section("Re-opened folders", reopened_lines)
    _section(f"Integrations on for {project}", state.integrations_for(p, project))
    return 0


register("allow-dir", _dir_build, _guarded(_allow_run))
register("never-dir", _dir_build, _guarded(_never_run))
register("reopen", _reopen_build, _guarded(_reopen_run))
register("integration", _integration_build, _guarded(_integration_run))
register("status", _status_build, _guarded(_status_run))
