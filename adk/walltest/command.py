"""adk wall-test: attack a throwaway project with headless Claude Code sessions."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import sys
from dataclasses import replace
from pathlib import Path

import adk
from adk.cli import register
from adk.paths import resolve
from adk.wall.generate import build_managed_settings, load_policy
from adk.walltest import runner
from adk.walltest.expect import Check, evaluate
from adk.walltest.fixture import build_fixture, remove_fixture, remove_leftovers


def _build(sub: argparse.ArgumentParser) -> None:
    which = sub.add_mutually_exclusive_group()
    which.add_argument("--settings", metavar="FILE", help="test this settings file instead of the packaged policy")
    which.add_argument("--installed", action="store_true",
                       help="pass no --settings and test whatever the machine has installed")
    sub.add_argument("--keep", action="store_true", help="keep the fixture and the generated settings")


def _online() -> bool:
    try:
        socket.create_connection(("registry.npmjs.org", 443), timeout=5).close()
    except OSError:
        return False
    return True


def _label(c: Check) -> str:
    return "WARN" if c.warning else ("PASS" if c.ok else "FAIL")


def _run_check(label: str, t: runner.Transcript) -> Check | None:
    if t.timed_out:
        return Check(f"run-completed:{label}", False, f"timed out after {runner.TIMEOUT_SECONDS}s")
    if t.returncode != 0:
        return Check(f"run-completed:{label}", False,
                     f"claude exited {t.returncode}: {t.stderr.strip()[-300:]}")
    return None


def _run(args: argparse.Namespace) -> int:
    p = resolve()
    repo = Path(adk.__file__).resolve().parent.parent
    work = repo / ".wall-work"
    work.mkdir(exist_ok=True)

    f = build_fixture(work, p)
    cleanup: list[Path] = []
    if args.installed:
        settings = None
    elif args.settings:
        settings = Path(args.settings).resolve()
    else:
        # Hooks run from this checkout, since the wall is not installed under /usr/local.
        candidate = build_managed_settings(load_policy(), replace(p, lib_dir=repo), os.environ.keys())
        settings = work / f"candidate-settings-{secrets.token_hex(4)}.json"
        settings.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
        cleanup.append(settings)

    sibling = work / f"sibling-{secrets.token_hex(4)}"
    sibling.mkdir()
    (sibling / "note.txt").write_text(f.control + "\n", encoding="utf-8")
    cleanup.append(sibling)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    try:
        shutil.copy(Path(__file__).with_name("probe.py"), f.project / "probe.py")
        config = {
            "project": str(f.project),
            "control": f.control,
            "canaries": {k: str(v) for k, v in f.canaries.items()},
            "canary_dir": str(p.canary_dir),
            "write_targets": {"write:" + k[5:]: str(v) for k, v in f.write_targets.items()
                              if k.startswith("bash:")},
            "outside_port": listener.getsockname()[1],
            "projects_dir": str(repo.parent),
            "repo_readme": str(repo / "README.md"),
            "sibling_file": str(sibling / "note.txt"),
        }
        (f.project / "probe-config.json").write_text(json.dumps(config), encoding="utf-8")

        env = {"ADK_CANARY_TOKEN": f.marker}
        online = _online()
        bash_run = runner.run_claude(runner.bash_prompt(), f.project, settings, env)
        tool_run = runner.run_claude(runner.tool_prompt(f), f.project, settings, env)

        report = runner.extract_probe_report(bash_run)
        if not online:
            report["_offline"] = True
        checks = evaluate(report, tool_run, f, bash_raw=bash_run.raw)
        for label, t in (("bash", bash_run), ("tool", tool_run)):
            bad = _run_check(label, t)
            if bad:
                checks.append(bad)
        if not online:
            checks.append(Check("network", True, "machine is offline: network results are measurements", warning=True))

        for c in checks:
            print(f"{_label(c)} {c.name}: {c.detail}")
        failed = [c for c in checks if not c.ok and not c.warning]
        print(f"{len(checks) - len(failed)} of {len(checks)} checks ok, {len(failed)} failed")
        return 1 if failed else 0
    finally:
        listener.close()
        remove_leftovers(f)
        if args.keep:
            print(f"kept: {f.project} (canary: {f.canaries['outside/secret.txt']})", file=sys.stderr)
        else:
            remove_fixture(f, p)
            for path in cleanup:
                shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)


register("wall-test", _build, _run)
