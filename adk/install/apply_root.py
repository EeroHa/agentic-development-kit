"""The only part of the installer that runs as root. Standard library only.

Usage: apply_root.py apply <staging> <backups> | restore <backups>
Targets must lie inside ALLOWED. Each apply that changes anything saves the old
state in <backups>/<timestamp>/: files/<target>, links.json, created.json.
"""
import json
import os
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

LIB = "usr/local/lib/agentic-dev-kit"
ALLOWED = ("etc/claude-code", "etc/apparmor.d/bwrap", LIB, "usr/local/bin/adk")


def _inside(rel: PurePosixPath) -> bool:
    return any(rel.is_relative_to(a) for a in ALLOWED)


def _check_target(rel) -> str:
    pp = PurePosixPath(rel) if isinstance(rel, str) else None
    if pp is None or pp.is_absolute() or ".." in pp.parts or not _inside(pp):
        raise ValueError(f"refusing target outside the allowed locations: {rel!r}")
    return rel


def _mkdirs(d: Path) -> None:
    if not d.is_dir():
        _mkdirs(d.parent)
        d.mkdir()
        d.chmod(0o755)
        if os.geteuid() == 0:
            os.lchown(d, 0, 0)


def _put(dest: Path, want: tuple) -> None:
    """Atomically make dest a file ("file", bytes, mode) or a symlink ("link", to)."""
    _mkdirs(dest.parent)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".adk-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(want[1] if want[0] == "file" else b"")
        if want[0] == "file":
            os.chmod(tmp, want[2])
        else:
            os.unlink(tmp)
            os.symlink(want[1], tmp)
        if os.geteuid() == 0:
            os.lchown(tmp, 0, 0)
        os.replace(tmp, dest)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise


def _state(dest: Path):
    if dest.is_symlink():
        return ("link", os.readlink(dest))
    if dest.is_file():
        return ("file", dest.read_bytes(), stat.S_IMODE(dest.stat().st_mode))
    return None


def _load(staging: Path, root: Path) -> list[tuple]:
    """Check the whole manifest and read its payloads before anything is written."""
    ops = []
    try:
        for e in json.loads((staging / "manifest.json").read_text()):
            rel, mode, link, name = _check_target(e["target"]), e["mode"], e["link_to"], e["payload"]
            if not isinstance(mode, int) or not 0 <= mode <= 0o777:
                raise ValueError(f"bad mode for {rel}")
            if e["remove"]:
                want = None
            elif link is not None:
                lp = Path(link)
                if not lp.is_absolute() or ".." in lp.parts or not lp.is_relative_to(root / LIB):
                    raise ValueError(f"refusing link destination: {link}")
                want = ("link", link)
            else:
                if Path(name).name != name or (staging / name).is_symlink():
                    raise ValueError(f"bad payload name: {name!r}")
                want = ("file", (staging / name).read_bytes(), mode)
            ops.append((rel, want))
    except (KeyError, TypeError, AttributeError) as err:
        raise ValueError(f"malformed manifest: {err!r}") from err
    return ops


def apply(staging: Path, backups: Path, root: Path = Path("/")) -> list[str]:
    root = Path(root)
    ops = _load(Path(staging), root)
    bdir, created, links, changed = None, [], {}, []
    try:
        for rel, want in ops:
            dest, old = root / rel, _state(root / rel)
            if old == want:
                continue
            if bdir is None:
                bdir = Path(backups) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                _mkdirs(bdir)
            if old is None:
                created.append(rel)
            elif old[0] == "link":
                links[rel] = old[1]
            else:
                _put(bdir / "files" / rel, old)
            if want is None:
                os.unlink(dest)
            else:
                _put(dest, want)
            changed.append(str(dest))
    finally:
        for name, data in (("links.json", links), ("created.json", created)):
            if bdir is not None:
                _put(bdir / name, ("file", json.dumps(data).encode(), 0o644))
    return changed


def _prune(d: Path, root: Path) -> None:
    """Remove empty directories, but only inside the allowed locations."""
    while d != root and _inside(PurePosixPath(d.relative_to(root))):
        try:
            d.rmdir()
        except OSError:
            return
        d = d.parent


def restore(backups: Path, root: Path = Path("/")) -> list[str]:
    root, sets = Path(root), []
    for bdir in sorted(Path(backups).iterdir(), reverse=True) if Path(backups).is_dir() else []:
        files = {str(f.relative_to(bdir / "files")): f for f in (bdir / "files").rglob("*") if f.is_file()}
        created = json.loads((bdir / "created.json").read_text())
        links = json.loads((bdir / "links.json").read_text())
        for rel in [*created, *links, *files]:
            _check_target(rel)
        sets.append((bdir, created, links, files))
    restored = []
    for bdir, created, links, files in sets:
        for rel in created:
            if os.path.lexists(root / rel):
                os.unlink(root / rel)
            _prune((root / rel).parent, root)
        for rel, to in links.items():
            _put(root / rel, ("link", to))
        for rel, f in files.items():
            _put(root / rel, _state(f))
        restored += [str(root / rel) for rel in [*created, *links, *files]]
        shutil.rmtree(bdir)
    if sets:
        try:
            Path(backups).rmdir()  # the folder apply created, if now empty
        except OSError:
            pass
    return restored


def main(argv: list[str]) -> int:
    try:
        if argv[:1] == ["apply"] and len(argv) == 3:
            lines = apply(Path(argv[1]), Path(argv[2]))
        elif argv[:1] == ["restore"] and len(argv) == 2:
            lines = restore(Path(argv[1]))
        else:
            raise ValueError("usage: apply <staging> <backups> | restore <backups>")
    except (ValueError, OSError) as err:
        print(f"apply_root: {err}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
