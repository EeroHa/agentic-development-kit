"""adk install: show the install plan, and apply or undo the user-scope part."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import adk
from adk.cli import register
from adk.commands import _err, _hidden_options, _paths
from adk.install import apply_user
from adk.install.plan import build_plan, render_diff, write_staging
from adk.jsonio import InvalidJson


def _build(sub: argparse.ArgumentParser) -> None:
    actions = sub.add_subparsers(dest="install_action", required=True)
    for name in ("plan", "apply-user", "restore-user"):
        _hidden_options(actions.add_parser(name))


def _run(args: argparse.Namespace) -> int:
    p = _paths(args)
    repo = Path(adk.__file__).resolve().parent.parent
    try:
        if args.install_action == "restore-user":
            done = apply_user.restore(p)
        else:
            changes = build_plan(p, repo, os.environ.keys())
            if args.install_action == "plan":
                print(render_diff(changes))
                print(write_staging(changes, p.config_dir / "staging", p.root))
                return 0
            done = apply_user.apply(changes, p)
    except (InvalidJson, ValueError, OSError) as e:
        return _err(str(e))
    print("\n".join(done) if done else "nothing to change")
    return 0


register("install", _build, _run)
