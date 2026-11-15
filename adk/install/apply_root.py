"""The only part of the installer that runs as root. Standard library only.
Usage: apply_root.py apply <staging> <backups> | restore <backups>. Targets must lie inside ALLOWED;
each apply saves the old state in <backups>/<timestamp>/ (files/, links.json, created.json)."""
import contextlib
import errno
import json
import os
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

LIB = "usr/local/lib/agentic-dev-kit"
ALLOWED = ("etc/claude-code", "etc/apparmor.d/bwrap", LIB, "usr/local/bin/adk")


def _inside(rel: PurePosixPath) -> bool:
    return any(rel.is_relative_to(a) for a in ALLOWED)


def _check_target(rel) -> str:
    if not isinstance(rel, str) or (pp := PurePosixPath(rel)).is_absolute() or ".." in pp.parts or not _inside(pp):
        raise ValueError(f"refusing target outside the allowed locations: {rel!r}")
    return rel


def _read_regular(path: Path) -> bytes:
    # O_NOFOLLOW: a symlink at the end raises OSError
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError(f"not a regular file: {path.name}")
        return f.read()


def _own(chown, target) -> None:
    if os.geteuid() == 0:
        chown(target, 0, 0)


def _walk(root: Path, rel, create: bool = False) -> bool:
    """Check each folder above root/rel from the top (root is "/", trusted); refuse any symlink. With create,
    make missing folders one by one, set existing allowed ones to 0755 root:root. False: a folder is missing."""
    cur, parts = root, PurePosixPath(rel).parent.parts
    for i, part in enumerate(parts):
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            if not create:
                return False
            os.mkdir(cur)
            st = None
        if st and stat.S_ISLNK(st.st_mode):
            raise OSError(errno.ELOOP, "refusing a symlink in the path", str(cur))
        if create and (st is None or _inside(PurePosixPath(*parts[:i + 1]))):
            fd = os.open(cur, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
            os.fchmod(fd, 0o755)
            _own(os.fchown, fd)
            os.close(fd)
    return True


def _put(dest: Path, want: tuple, root: Path) -> None:
    """Atomically make dest a file ("file", bytes, mode) or a symlink ("link", to)."""
    _walk(root, dest.relative_to(root), create=True)
    tmp = dest.parent / f".adk-{os.urandom(6).hex()}"
    try:
        if want[0] == "file":
            with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as f:
                f.write(want[1])
            os.chmod(tmp, want[2])
        else:
            os.symlink(want[1], tmp)
        _own(os.lchown, tmp)
        os.replace(tmp, dest)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


def _state(dest: Path):
    """What dest holds now. As root, an entry owned by anyone else never matches, so it is rewritten."""
    if not os.path.lexists(dest):
        return None
    extra = ("foreign owner",) if os.geteuid() == 0 and os.lstat(dest).st_uid != 0 else ()
    if dest.is_symlink():
        return ("link", os.readlink(dest), *extra)
    if dest.is_file():
        return ("file", dest.read_bytes(), stat.S_IMODE(dest.stat().st_mode), *extra)


def _load(staging: Path, root: Path) -> list[tuple]:
    """Check the whole manifest and read its payloads before anything is written."""
    ops = []
    try:
        for e in json.loads(_read_regular(staging / "manifest.json")):
            rel, mode, link, name = _check_target(e["target"]), e["mode"], e["link_to"], e["payload"]
            _walk(root, rel)  # a symlinked folder above the target is refused before anything is written
            if not isinstance(mode, int) or mode & ~0o755:
                raise ValueError(f"bad mode for {rel}: group or other may not write")
            if e["remove"]:
                want = None
            elif link is not None:
                if not (lp := Path(link)).is_absolute() or ".." in lp.parts or not lp.is_relative_to(root / LIB):
                    raise ValueError(f"refusing link destination: {link}")
                want = ("link", link)
            else:
                if Path(name).name != name:
                    raise ValueError(f"bad payload name: {name!r}")
                want = ("file", _read_regular(staging / name), mode)
            ops.append((rel, want))
    except (KeyError, TypeError, AttributeError) as err:
        raise ValueError(f"malformed manifest: {err!r}") from err
    return ops


def apply(staging: Path, backups: Path, root: Path = Path("/")) -> list[str]:
    root = Path(root)
    ops = _load(Path(staging), root)
    broot = Path(backups).parent  # backup sets go through the same walk
    bdir, created, links, changed = None, [], {}, []
    try:
        for rel, want in ops:
            dest, old = root / rel, _state(root / rel)
            if old == want:
                continue
            if bdir is None:
                bdir = Path(backups) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                broot.mkdir(parents=True, exist_ok=True)
            if old is None:
                created.append(rel)
            elif old[0] == "link":
                links[rel] = old[1]
            else:
                _put(bdir / "files" / rel, old, broot)
            if want is None:
                os.unlink(dest)
            else:
                _put(dest, want, root)
            changed.append(str(dest))
    finally:
        for name, data in (("links.json", links), ("created.json", created)):
            if bdir is not None:
                _put(bdir / name, ("file", json.dumps(data).encode(), 0o644), broot)
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
    root, sets, restored = Path(root), [], []
    for bdir in sorted(Path(backups).iterdir(), reverse=True) if Path(backups).is_dir() else []:
        files = {str(f.relative_to(bdir / "files")): f for f in (bdir / "files").rglob("*") if f.is_file()}
        created, links = (json.loads((bdir / n).read_text()) for n in ("created.json", "links.json"))
        for rel in [*created, *links, *files]:
            _walk(root, _check_target(rel))  # refuse a symlinked folder before anything is restored
        sets.append((bdir, created, links, files))
    for bdir, created, links, files in sets:
        for rel in created:
            if _walk(root, rel) and os.path.lexists(root / rel):
                os.unlink(root / rel)
            _prune((root / rel).parent, root)
        for rel, to in links.items():
            _put(root / rel, ("link", to), root)
        for rel, f in files.items():
            _put(root / rel, _state(f), root)
        restored += [str(root / rel) for rel in [*created, *links, *files]]
        shutil.rmtree(bdir)
    with contextlib.suppress(OSError):
        Path(backups).rmdir()  # the folder apply created, if now empty
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
