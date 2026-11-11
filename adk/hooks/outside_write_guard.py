"""PreToolUse hook: guard Edit/Write/NotebookEdit targets outside the project."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adk.hooks.common import ask, deny, run
from adk.paths import Paths
from adk.state import allowed_dirs, load_lists

PROTECTED_NAMES: tuple[str, ...] = (
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".mcp.json",
    # core.hooksPath, core.fsmonitor and friends make git run arbitrary commands.
    ".git/config",
)


def _temp_roots() -> list[Path]:
    return [Path(tempfile.gettempdir()).resolve()]


def _res(d) -> Path:
    return Path(d).expanduser().resolve()


def _inside(target: Path, base: Path) -> bool:
    return target == base or base in target.parents


def _has_run(parts: tuple[str, ...], run_: tuple[str, ...]) -> bool:
    n = len(run_)
    return any(parts[i:i + n] == run_ for i in range(len(parts) - n + 1))


def _is_protected(target: Path, p: Paths) -> bool:
    parts = target.parts
    for name in PROTECTED_NAMES:
        want = tuple(name.split("/"))
        if parts[-len(want):] == want:
            return True
    if _has_run(parts, (".git", "hooks")):
        return True
    for d in (p.config_dir, p.lib_dir, p.managed_dir):
        if _inside(target, _res(d)):
            return True
    return target == _res(p.claude_user_settings)


def _target_path(event: dict) -> str | None:
    ti = event.get("tool_input")
    if not isinstance(ti, dict):
        return None
    keys = ("notebook_path", "file_path") if event.get("tool_name") == "NotebookEdit" else ("file_path",)
    for k in keys:
        v = ti.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _nonempty_str(v) -> str | None:
    return v if isinstance(v, str) and v else None


def decide(event: dict, p: Paths) -> dict | None:
    raw = _target_path(event)
    if raw is None:
        return deny("adk: write target missing from hook input")
    cwd = _nonempty_str(event.get("cwd"))
    project = _nonempty_str(event.get("_project_dir")) or cwd
    if project is None:
        return deny("adk: project directory missing from hook input")

    # Resolve symlinks on the deepest existing ancestor; ".." is folded by resolve().
    given = Path(raw).expanduser()
    if not given.is_absolute():
        given = Path(cwd or project) / given
    target = given.resolve()
    # Protected-name checks also see the path as given (symlinks not followed), so a
    # symlinked settings file cannot hide behind its destination.
    lexical = Path(os.path.normpath(given))
    if _is_protected(target, p) or _is_protected(lexical, p):
        return deny(f"adk: {target} is a protected configuration path")

    safe = [_res(project)]
    scratch = _nonempty_str(event.get("scratchpad_dir"))
    if scratch:
        safe.append(_res(scratch))
    safe.extend(_temp_roots())
    safe.extend(_res(d) for d in allowed_dirs(p))
    if any(_inside(target, s) for s in safe):
        return None

    for d in load_lists(p)["never_dirs"]:
        if _inside(target, _res(d)):
            return deny(f"adk: {_res(d)} is on the never-allow list")

    return ask(
        f"adk: write outside the project: {target}. Allow once here, or run "
        f"`adk allow-dir {target.parent}` or `adk never-dir {target.parent}` "
        "yourself to decide for good."
    )


if __name__ == "__main__":
    sys.exit(run(decide))
