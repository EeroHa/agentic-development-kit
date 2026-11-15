"""Work out what the installer would change, show it, and stage the root part."""
from __future__ import annotations

import difflib
import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from adk.jsonio import write_json_atomic
from adk.paths import Paths
from adk.wall.generate import build_managed_settings, load_policy

APPARMOR_PROFILE = """\
abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap>
}
"""

LAUNCHER = """\
#!/usr/bin/python3 -I
import sys
sys.path.insert(0, "<LIB>")
from adk.cli import main
sys.exit(main())
"""

DEFAULT_CONFIG = {
    "weekly_reserve_percent": 80,
    "roles": {"planner": "opus", "orchestrator": "opus", "builder": "sonnet", "helper": "haiku"},
    "telegram": {"chat_id": None},
}
CANARY_TEXT = "ADK-CANARY-INSTALLED\n"


@dataclass(frozen=True)
class FileChange:
    target: Path
    new_bytes: bytes | None      # None = remove the target
    mode: int
    scope: str                   # "root" or "user"
    link_to: Path | None = None  # set = the target is a symlink to this; new_bytes is b""


def _json_text(data: dict) -> bytes:
    return (json.dumps(data, indent=2) + "\n").encode()


def _package_files(repo: Path) -> list[Path]:
    pkg = Path(repo) / "adk"
    return sorted(
        f for f in pkg.rglob("*")
        if f.is_file() and "__pycache__" not in f.parts
        and (f.suffix == ".py" or f.name == "policy.json")
    )


def _stale_files(installed_pkg: Path, keep: set[Path]) -> list[Path]:
    found = []
    for folder, _dirs, names in os.walk(installed_pkg):
        for name in names:
            f = Path(folder) / name
            if f not in keep and not f.is_symlink():
                found.append(f)
    return sorted(found)


def build_plan(p: Paths, repo: Path, env_names: Iterable[str]) -> list[FileChange]:
    repo = Path(repo)
    lib_pkg = p.lib_dir / "adk"
    dropin = json.dumps(build_managed_settings(load_policy(), p, env_names), indent=2) + "\n"
    launcher = p.lib_dir / "bin" / "adk"

    root = [FileChange(p.apparmor_profile, APPARMOR_PROFILE.encode(), 0o644, "root")]
    copied = [(lib_pkg / f.relative_to(repo / "adk"), f.read_bytes()) for f in _package_files(repo)]
    root += [FileChange(t, data, 0o644, "root") for t, data in copied]
    root += [FileChange(t, None, 0o644, "root")
             for t in _stale_files(lib_pkg, {t for t, _ in copied})]
    root += [
        FileChange(launcher, LAUNCHER.replace("<LIB>", str(p.lib_dir)).encode(), 0o755, "root"),
        FileChange(p.bin_link, b"", 0o755, "root", link_to=launcher),
        FileChange(p.managed_dropin, dropin.encode(), 0o644, "root"),
    ]

    user = []
    for target, data in (
        (p.config_file, _json_text(DEFAULT_CONFIG)),
        (p.secrets_file, b""),
        (p.lists_file, _json_text({"never_dirs": [], "integrations": {}})),
    ):
        if not os.path.lexists(target):
            user.append(FileChange(target, data, 0o600, "user"))
    user.append(FileChange(p.canary_dir / "secret.txt", CANARY_TEXT.encode(), 0o600, "user"))
    return root + user


def is_unchanged(c: FileChange) -> bool:
    """True when the target already has exactly what the change would give it."""
    t = c.target
    if c.new_bytes is None:
        return not os.path.lexists(t)
    if c.link_to is not None:
        return t.is_symlink() and os.readlink(t) == str(c.link_to)
    return (
        t.is_file() and not t.is_symlink()
        and t.read_bytes() == c.new_bytes
        and stat.S_IMODE(t.stat().st_mode) == c.mode
    )


def _current(t: Path) -> bytes | None:
    return t.read_bytes() if t.is_file() and not t.is_symlink() else None


def _lines(data: bytes) -> list[str] | None:
    try:
        return data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None


def _render_one(c: FileChange) -> list[str]:
    t = c.target
    out = [f"=== {t} ({c.scope})"]
    if c.link_to is not None:
        out.append(f"(link -> {c.link_to})")
    elif c.new_bytes is None:
        out.append("(removed)")
    if is_unchanged(c):
        return out + ["(unchanged)"]
    if t.is_symlink() and c.link_to is None:
        out.append(f"(replaces link -> {os.readlink(t)})")
    if c.link_to is not None:
        return out
    old = _current(t)
    if old is None and c.new_bytes is not None:
        out.append("(new file)")
    if old is not None and old == c.new_bytes:
        return out + [f"(mode {stat.S_IMODE(t.stat().st_mode):04o} -> {c.mode:04o})"]
    new = c.new_bytes or b""
    old_lines, new_lines = _lines(old or b""), _lines(new)
    if old_lines is None or new_lines is None:
        return out + [f"(binary content: {len(old or b'')} -> {len(new)} bytes)"]
    return out + list(difflib.unified_diff(
        old_lines, new_lines,
        "/dev/null" if old is None else str(t),
        "/dev/null" if c.new_bytes is None else str(t),
        lineterm=""))


def render_diff(changes: list[FileChange]) -> str:
    return "\n".join(line for c in changes for line in _render_one(c)) + "\n"


def write_staging(changes: list[FileChange], staging: Path, root: Path = Path("/")) -> Path:
    """Write the root-scope changes for apply_root.py; targets are relative to root."""
    staging = Path(staging)
    staging.mkdir(parents=True, exist_ok=True)
    for child in staging.iterdir():
        shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
    manifest = []
    payloads = 0
    for c in (c for c in changes if c.scope == "root"):
        payload = None
        if c.new_bytes is not None and c.link_to is None:
            payload = f"{payloads:04d}"
            payloads += 1
            (staging / payload).write_bytes(c.new_bytes)
        manifest.append({
            "target": str(c.target.relative_to(root)),
            "payload": payload,
            "mode": c.mode,
            "link_to": None if c.link_to is None else str(c.link_to),
            "remove": c.new_bytes is None,
        })
    write_json_atomic(staging / "manifest.json", manifest)
    return staging
