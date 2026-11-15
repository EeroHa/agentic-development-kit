"""Export one owner-approved commit of the kit, so the installer runs reviewed code only.

Standard library only. install.sh runs this file (from the approved commit) as
    export.py info   <repo> <hash>          print the commit subject
    export.py export <repo> <dest> <hash>   replace <dest> with the commit's adk/ and install.sh
    export.py verify <repo> <dest> <hash>   check <dest> again, right before it is run

Nothing here reads HEAD, a branch or the working tree: the owner types the full
commit hash and that is the only input. Every git call runs with
GIT_NO_REPLACE_OBJECTS=1, and a repository that has any refs/replace entry is
refused, because a replace ref makes git serve other bytes under the same hash.
"""
import hashlib
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile

TRACKED = ("adk", "install.sh")
HASH_RE = re.compile(r"[0-9a-f]{40}")
REQUIRED = ("adk/__init__.py", "adk/wall/policy.json")
_DROP_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
             "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE")


def _git(repo, *args) -> bytes:
    env = {k: v for k, v in os.environ.items() if k not in _DROP_ENV}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    done = subprocess.run(["git", "-C", os.fspath(repo), *args], capture_output=True, check=True, env=env)
    return done.stdout


def refuse_replace_refs(repo) -> None:
    """Refuse when the repository has any refs/replace entry, loose, packed or listed by git."""
    for d in {_git(repo, "rev-parse", "--git-dir").decode().strip(),
              _git(repo, "rev-parse", "--git-common-dir").decode().strip()}:
        base = os.path.join(os.fspath(repo), d)
        loose = os.path.join(base, "refs", "replace")
        if os.path.lexists(loose) and (not os.path.isdir(loose) or os.listdir(loose)):
            raise ValueError(f"refusing: {loose} is not empty (git replace refs can swap the files)")
        try:
            with open(os.path.join(base, "packed-refs"), "rb") as f:
                if b"refs/replace/" in f.read():
                    raise ValueError("refusing: packed-refs contains refs/replace/ entries")
        except FileNotFoundError:
            pass
    if _git(repo, "for-each-ref", "refs/replace").strip():
        raise ValueError("refusing: git for-each-ref refs/replace lists entries")


def check_commit(repo, commit: str) -> str:
    """Refuse a typed hash that is not a full commit hash in repo; return the commit subject."""
    if not HASH_RE.fullmatch(commit):
        raise ValueError("the commit must be 40 lowercase hex characters")
    refuse_replace_refs(repo)
    try:
        kind = _git(repo, "cat-file", "-t", commit).decode().strip()
    except subprocess.CalledProcessError:
        kind = "missing"
    if kind != "commit":
        raise ValueError(f"{commit} is not a commit in this repository ({kind})")
    return _git(repo, "show", "-s", "--format=%s", commit).decode().strip()


def _expected(repo, commit) -> dict[str, tuple[str, bool]]:
    """{path: (blob id, executable)} for the exported paths, read from the commit's own tree."""
    tree = _git(repo, "rev-parse", "--verify", f"{commit}^{{tree}}").decode().strip()
    out = {}
    for entry in _git(repo, "ls-tree", "-r", "-z", tree, "--", *TRACKED).split(b"\0"):
        if not entry:
            continue
        meta, path = entry.decode().split("\t", 1)
        mode, _kind, blob = meta.split()
        if mode not in ("100644", "100755"):
            raise ValueError(f"refusing {path}: only plain files are exported (mode {mode})")
        out[path] = (blob, mode == "100755")
    return out


def _checked(path, euid, what, root_ok=False):
    st = os.lstat(path)
    if st.st_uid != euid and not (root_ok and st.st_uid == 0):
        raise ValueError(f"{what} {path} is not owned by you")
    if st.st_mode & 0o022:
        raise ValueError(f"{what} {path} is writable by group or others")
    return st


def verify_export(repo, dest, commit) -> None:
    """Check that dest holds exactly the commit's files, and only the owner can change them.

    Why this is enough, instead of rebuilding the commit's tree from the files: the
    export is a subset of the commit (adk/ and install.sh), so its tree id can never
    equal the commit's tree id. Instead every extracted file's git blob id is compared
    with the entry in `git ls-tree -r <commit>^{tree}`. Equal blob ids for every path
    in the commit's own tree prove the contents, and the exact path set closes the gap
    that export-ignore (in .gitattributes or .git/info/attributes) silently drops files.
    """
    check_commit(repo, commit)
    want, euid, dest = _expected(repo, commit), os.geteuid(), os.fspath(dest)
    for need in REQUIRED:
        if need not in want:
            raise ValueError(f"the commit is missing {need}")
    _checked(os.path.dirname(dest), euid, "the folder above the export", root_ok=True)
    found = {}
    for folder, dirs, names in os.walk(dest):
        if not stat.S_ISDIR(_checked(folder, euid, "folder").st_mode):
            raise ValueError(f"not a folder: {folder}")
        for name in dirs + names:
            full = os.path.join(folder, name)
            if os.path.islink(full):
                raise ValueError(f"the export holds a symlink: {full}")
        for name in names:
            full = os.path.join(folder, name)
            st = _checked(full, euid, "file")
            if not stat.S_ISREG(st.st_mode):
                raise ValueError(f"the export holds a non-regular file: {full}")
            with open(full, "rb") as f:
                data = f.read()
            found[os.path.relpath(full, dest)] = (
                hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(), bool(st.st_mode & 0o100))
    if found != want:
        extra, missing = sorted(set(found) - set(want)), sorted(set(want) - set(found))
        differ = sorted(p for p in set(found) & set(want) if found[p] != want[p])
        raise ValueError(f"the export differs from the commit: extra {extra}, missing {missing}, changed {differ}")


def export_commit(repo, dest, commit) -> str:
    """Replace dest with the copy of adk/ and install.sh in commit; return the hash."""
    check_commit(repo, commit)
    _expected(repo, commit)  # refuses links and other non-plain entries before anything is extracted
    repo, dest = os.fspath(repo), os.fspath(dest)
    archive = _git(repo, "archive", "--format=tar", commit, *TRACKED)
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
    os.mkdir(dest, 0o700)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(dest, filter="data")
        for folder, dirs, names in os.walk(dest):  # git archive writes 0775 folders: fix the modes
            for d in dirs:
                os.chmod(os.path.join(folder, d), 0o755)
            for n in names:
                f = os.path.join(folder, n)
                os.chmod(f, 0o755 if os.stat(f).st_mode & 0o100 else 0o644)
        verify_export(repo, dest, commit)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return commit


def main(argv) -> int:
    try:
        if len(argv) == 3 and argv[0] == "info":
            print(check_commit(argv[1], argv[2]))
        elif len(argv) == 4 and argv[0] == "export":
            export_commit(argv[1], argv[2], argv[3])
        elif len(argv) == 4 and argv[0] == "verify":
            verify_export(argv[1], argv[2], argv[3])
        else:
            print("usage: export.py info <repo> <hash> | export <repo> <dest> <hash> | verify <repo> <dest> <hash>",
                  file=sys.stderr)
            return 2
    except (subprocess.CalledProcessError, OSError, ValueError, tarfile.TarError) as err:
        print(f"export failed: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
