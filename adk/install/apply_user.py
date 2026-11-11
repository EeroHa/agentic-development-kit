"""Apply and undo the user-scope part of the installer (files in ~/.config/adk)."""
from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from adk.install.plan import FileChange, is_unchanged
from adk.paths import Paths


def _inside_config(p: Paths, target: Path) -> bool:
    return ".." not in target.parts and target.is_relative_to(p.config_dir) and target != p.config_dir


def _write(dest: Path, data: bytes, mode: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".adk-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, dest)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise


def apply(changes: list[FileChange], p: Paths) -> list[str]:
    """Apply the user-scope changes; returns the targets that changed."""
    mine = [c for c in changes if c.scope == "user"]
    for c in mine:
        if not _inside_config(p, c.target):
            raise ValueError(f"refusing user-scope target outside {p.config_dir}: {c.target}")
        if c.new_bytes is None or c.link_to is not None:
            raise ValueError(f"user scope only writes plain files: {c.target}")
    bdir, created, changed = None, [], []
    try:
        for c in mine:
            if is_unchanged(c):
                continue
            rel = str(c.target.relative_to(p.config_dir))
            if bdir is None:
                bdir = p.backups_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                bdir.mkdir(parents=True)
            if c.target.is_file() and not c.target.is_symlink():
                _write(bdir / "files" / rel, c.target.read_bytes(), stat.S_IMODE(c.target.stat().st_mode))
            elif not os.path.lexists(c.target):
                created.append(rel)
            _write(c.target, c.new_bytes, c.mode)
            changed.append(str(c.target))
    finally:
        if bdir is not None:
            (bdir / "created.json").write_text(json.dumps(created))
    return changed


def restore(p: Paths) -> list[str]:
    """Undo every user-scope apply, newest first, and delete the backup sets."""
    restored = []
    sets = sorted(p.backups_dir.iterdir(), reverse=True) if p.backups_dir.is_dir() else []
    for bdir in sets:
        for rel in json.loads((bdir / "created.json").read_text()):
            _inside_or_raise(p, p.config_dir / rel)
            (p.config_dir / rel).unlink(missing_ok=True)
            restored.append(str(p.config_dir / rel))
            _prune(p, (p.config_dir / rel).parent)
        for f in (bdir / "files").rglob("*"):
            if f.is_file():
                target = p.config_dir / f.relative_to(bdir / "files")
                _inside_or_raise(p, target)
                _write(target, f.read_bytes(), stat.S_IMODE(f.stat().st_mode))
                restored.append(str(target))
        shutil.rmtree(bdir)
    _prune(p, p.backups_dir)
    return restored


def _inside_or_raise(p: Paths, target: Path) -> None:
    if not _inside_config(p, target):
        raise ValueError(f"refusing backup entry outside {p.config_dir}: {target}")


def _prune(p: Paths, d: Path) -> None:
    """Remove empty directories up to and including the config folder."""
    while d.is_relative_to(p.config_dir):
        try:
            d.rmdir()
        except OSError:
            return
        d = d.parent
