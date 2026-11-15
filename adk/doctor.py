"""adk doctor: confirm the installed wall is present, current and working. Changes nothing."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Callable, Mapping

from adk import state
from adk.cli import register
from adk.commands import _hidden_options, _paths
from adk.paths import Paths
from adk.wall.generate import build_managed_settings, credential_like, load_policy
from adk.walltest.expect import Check

TESTED_CLAUDE = "2.1.322"
TESTED_SUPERPOWERS = "7.0.0"

# Common tool folders under home that the wall closes by default.
TOOL_FOLDERS: tuple[str, ...] = (
    ".cargo", ".rustup", ".pyenv", ".rbenv", ".sdkman", ".volta", ".deno",
    ".local/share/uv", ".cache/uv", ".config/nvm", ".m2", ".cache/go-build", "go",
)


def _apparmor(p: Paths) -> Check:
    ok = p.apparmor_profile.exists()
    return Check("apparmor-profile", ok, str(p.apparmor_profile) if ok else f"missing: {p.apparmor_profile}")


def _bwrap(which: Callable[[str], str | None]) -> Check:
    exe = which("bwrap")
    if exe is None:
        return Check("bwrap-works", False, "bwrap not found")
    try:
        r = subprocess.run([exe, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "true"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Check("bwrap-works", False, str(e))
    if r.returncode == 0:
        return Check("bwrap-works", True, "a sandbox starts")
    return Check("bwrap-works", False, f"exit {r.returncode}: {r.stderr.strip()[-200:]}")


def _read_dropin(p: Paths) -> dict | None:
    try:
        doc = json.loads(p.managed_dropin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _managed_installed(p: Paths, env: Mapping[str, str]) -> Check:
    if not p.managed_dropin.exists():
        return Check("managed-installed", False, f"missing: {p.managed_dropin}; run the installer")
    expected = build_managed_settings(load_policy(), p, env.keys())
    if _read_dropin(p) != expected:
        return Check("managed-installed", False, "wall is out of date, re-run the installer")
    return Check("managed-installed", True, str(p.managed_dropin))


def _owned_paths(p: Paths) -> list[Path]:
    """The drop-in and lib_dir, plus every file and folder inside lib_dir and managed-settings.d."""
    found = [p.managed_dropin, p.lib_dir]
    for top in (p.lib_dir, p.managed_dropin.parent):
        for folder, dirs, names in os.walk(top):
            found += [Path(folder) / n for n in (*dirs, *names)]
        if top != p.lib_dir:
            found.append(top)
    return list(dict.fromkeys(found))


def _root_owned(p: Paths) -> Check:
    problems: list[str] = []
    skipped = False
    for path in _owned_paths(p):
        try:
            st = os.lstat(path)
        except OSError:
            problems.append(f"missing: {path}")
            continue
        if not stat.S_ISLNK(st.st_mode) and st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            problems.append(f"group- or world-writable: {path}")
        if st.st_uid != 0:
            # Under a test root the files belong to the test user; only the real root is held to uid 0.
            if p.root != Path("/") and st.st_uid == os.geteuid() and os.geteuid() != 0:
                skipped = True
            else:
                problems.append(f"not owned by root: {path}")
    if problems:
        shown = "; ".join(problems[:5]) + (f"; and {len(problems) - 5} more" if len(problems) > 5 else "")
        return Check("managed-root-owned", False, shown)
    if skipped:
        return Check("managed-root-owned", True, "ownership not checked (not root, test paths)", warning=True)
    return Check("managed-root-owned", True, "root-owned and not writable by others")


def _new_credential_names(p: Paths, env: Mapping[str, str]) -> Check:
    policy = load_policy()
    doc = _read_dropin(p) or {}
    entries = doc.get("sandbox", {}).get("credentials", {}).get("envVars", []) if isinstance(doc.get("sandbox"), dict) else []
    denied = {e.get("name") for e in entries if isinstance(e, dict)}
    new = [n for n in credential_like(env.keys(), policy)
           if n not in denied and n not in policy["env_never_deny"]]
    if new:
        return Check("new-credential-names", False,
                     "not denied by the installed wall: " + ", ".join(new) + "; re-run the installer")
    return Check("new-credential-names", True, "every credential-like variable is denied")


def _api_key(env: Mapping[str, str]) -> Check:
    if "ANTHROPIC_API_KEY" in env:
        return Check("api-key-in-env", False, "ANTHROPIC_API_KEY is set: headless runs would be billed to the API")
    return Check("api-key-in-env", True, "not set")


def _claude_version(which: Callable[[str], str | None]) -> str | None:
    exe = which("claude")
    if exe is None:
        return None
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return r.stdout.split()[0]


def _superpowers_version(p: Paths) -> str | None:
    base = p.home / ".claude" / "plugins" / "cache" / "claude-plugins-official" / "superpowers"
    try:
        folders = sorted(d for d in base.iterdir() if d.is_dir())
    except OSError:
        return None
    for d in reversed(folders):
        try:
            v = json.loads((d / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version")
        except (OSError, ValueError, AttributeError):
            v = None
        return str(v) if v else d.name
    return None


def _versions(p: Paths, which: Callable[[str], str | None]) -> Check:
    claude, sp = _claude_version(which), _superpowers_version(p)
    parts: list[str] = []
    warn = False
    for label, found, tested in (("claude", claude, TESTED_CLAUDE), ("superpowers", sp, TESTED_SUPERPOWERS)):
        if found is None:
            parts.append(f"{label} not found (tested {tested})")
            warn = True
        elif found != tested:
            parts.append(f"{label} {found} (tested {tested})")
            warn = True
        else:
            parts.append(f"{label} {found}")
    return Check("versions", True, "; ".join(parts), warning=warn)


def _closed_tool_folders(p: Paths) -> Check:
    policy = load_policy()
    ro = state.reopened(p)
    skip: set[Path] = set()
    for entry in [*policy["secret_dirs"], *policy["reopen_read"], *ro["read"], *ro["write"]]:
        e = str(entry)
        skip.add((p.home / e[2:]) if e.startswith("~/") else Path(e))
    closed = [str(p.home / f) for f in TOOL_FOLDERS if (p.home / f).is_dir() and (p.home / f) not in skip]
    if not closed:
        return Check("closed-tool-folders", True, "no common tool folder is closed")
    return Check("closed-tool-folders", True,
                 "closed to Claude, if a tool needs one: " + "; ".join(f"adk reopen {f}" for f in closed),
                 warning=True)


def run_static_checks(p: Paths, env: Mapping[str, str], which: Callable[[str], str | None]) -> list[Check]:
    return [
        _apparmor(p),
        _bwrap(which),
        _managed_installed(p, env),
        _root_owned(p),
        _new_credential_names(p, env),
        _api_key(env),
        _versions(p, which),
        _closed_tool_folders(p),
    ]


def _label(c: Check) -> str:
    return "WARN" if c.warning else ("OK" if c.ok else "FAIL")


def _build(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--quick", action="store_true", help="skip the wall test")
    _hidden_options(sub)


def _run(args: argparse.Namespace) -> int:
    p = _paths(args)
    checks = run_static_checks(p, os.environ, shutil.which)
    for c in checks:
        print(f"{_label(c)} {c.name}: {c.detail}")
    failed = [c for c in checks if not c.ok and not c.warning]
    code = 1 if failed else 0
    if not args.quick:
        from adk.walltest.command import _run as wall_test
        print("wall-test --installed:")
        if wall_test(argparse.Namespace(installed=True, settings=None, keep=False)) != 0:
            code = 1
    return code


register("doctor", _build, _run)
