"""Export one owner-approved commit of the kit, so the installer runs reviewed code only.

Standard library only. install.sh runs this file (from the approved commit) as
    export.py info   <repo> <hash>          print the commit subject
    export.py export <repo> <dest> <hash>   replace <dest> with the commit's adk/ and install.sh
    export.py verify <repo> <dest> <hash>   check <dest> again, right before it is run

Nothing here reads HEAD, a branch or the working tree: the owner types the full
commit hash and that is the only input.

Git does not check the hash of a subtree or a blob when it reads one, and Claude Code can
write to .git/objects. So git's object reads are not trusted. The export walks from the
typed commit, re-reads the raw bytes of every commit, tree and blob in the export set with
`git cat-file <type> <id>`, recomputes sha1(b"<type> <len>\\0" + bytes) in Python and
refuses on any difference. The files are written from those verified bytes. `git archive`
is not used (it applies attributes and filters). `git fsck --no-dangling` must be silent.
Every git call runs with GIT_NO_REPLACE_OBJECTS=1, GIT_DIR pinned to the repository's own
git dir, and all other GIT_* variables removed. A repository that redirects its objects or
history (commondir, alternates, grafts, shallow, replace refs) is refused. .git/info/attributes
and .gitattributes are never read, because the export uses no filter.

Limit: the check is only as strong as SHA-1. Someone who can produce a SHA-1 collision with
a reviewed object could still swap it. That is out of reach of a file write, and not closed here.
"""
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys

TRACKED = ("adk", "install.sh")
HASH_RE = re.compile(r"[0-9a-f]{40}")
REQUIRED = ("adk/__init__.py", "adk/wall/policy.json")
# Files whose existence makes git read objects, history or config from somewhere else.
REFUSED_FILES = ("commondir", "objects/info/alternates", "info/grafts", "shallow")
FILE_MODES = {"100644": False, "100755": True}
TREE_MODE = "40000"


def _env(gitdir=None) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    if gitdir is not None:
        env["GIT_DIR"] = gitdir
    return env


class Repo:
    """A repository, addressed only by its pinned git dir."""

    def __init__(self, path):
        path = os.fspath(path)
        if not os.path.isdir(path):
            raise ValueError(f"not a folder: {path}")
        done = subprocess.run(
            ["git", "-C", path, "rev-parse", "--path-format=absolute", "--absolute-git-dir", "--git-common-dir"],
            capture_output=True, env=_env())
        lines = done.stdout.decode(errors="replace").split("\n")
        if done.returncode != 0 or len(lines) < 2:
            raise ValueError(f"{path} is not a git repository")
        self.gitdir, self.common = lines[0], lines[1]

    def git(self, *args) -> bytes:
        done = subprocess.run(["git", *args], capture_output=True, env=_env(self.gitdir), cwd=self.gitdir)
        if done.returncode != 0:
            raise subprocess.CalledProcessError(done.returncode, ["git", *args], done.stdout, done.stderr)
        return done.stdout


def _open(repo) -> Repo:
    return repo if isinstance(repo, Repo) else Repo(repo)


def refuse_unsafe_repo(repo) -> None:
    """Refuse a repository that makes git serve objects, history or config from anywhere but its own folder."""
    repo = _open(repo)
    if os.path.realpath(repo.common) != os.path.realpath(repo.gitdir):
        raise ValueError(f"refusing: the common git dir {repo.common} differs from the git dir {repo.gitdir}")
    for rel in REFUSED_FILES:
        if os.path.lexists(os.path.join(repo.gitdir, rel)):
            raise ValueError(f"refusing: {os.path.join(repo.gitdir, rel)} exists (it makes git read from elsewhere)")
    loose = os.path.join(repo.gitdir, "refs", "replace")
    if os.path.lexists(loose) and (not os.path.isdir(loose) or os.listdir(loose)):
        raise ValueError(f"refusing: {loose} is not empty (git replace refs can swap the files)")
    try:
        with open(os.path.join(repo.gitdir, "packed-refs"), "rb") as f:
            if b"refs/replace/" in f.read():
                raise ValueError("refusing: packed-refs contains refs/replace/ entries")
    except FileNotFoundError:
        pass
    if repo.git("for-each-ref", "refs/replace").strip():
        raise ValueError("refusing: git for-each-ref refs/replace lists entries")


def run_fsck(repo) -> None:
    """git fsck --no-dangling must say nothing: it is the one check that covers every object, not only the export set."""
    repo = _open(repo)
    done = subprocess.run(["git", "fsck", "--no-dangling", "--no-progress"], capture_output=True,
                          env=_env(repo.gitdir), cwd=repo.gitdir)
    said = (done.stdout + done.stderr).decode(errors="replace").strip()
    if done.returncode != 0 or said:
        first = "; ".join(said.splitlines()[:3])
        raise ValueError(f"refusing: git fsck --no-dangling reports problems (exit {done.returncode}): {first}")


def _read_verified(repo: Repo, kind: str, oid: str, verified: dict) -> bytes:
    """The raw bytes of one object, accepted only if sha1(header + bytes) is its id."""
    if oid in verified:
        return verified[oid][1]
    try:
        data = repo.git("cat-file", kind, oid)
    except subprocess.CalledProcessError:
        raise ValueError(f"refusing: cannot read {kind} {oid}") from None
    if hashlib.sha1(b"%s %d\0" % (kind.encode(), len(data)) + data).hexdigest() != oid:
        raise ValueError(f"refusing: {kind} {oid} does not match its own hash (the object was replaced)")
    verified[oid] = (kind, data)
    return data


def _parse_tree(data: bytes) -> dict:
    """{name: (mode, id)} from the raw bytes of a tree object."""
    out, i = {}, 0
    while i < len(data):
        sp = data.index(b" ", i)
        nul = data.index(b"\0", sp)
        mode, name, raw = data[i:sp].decode("ascii"), data[sp + 1:nul].decode("utf-8"), data[nul + 1:nul + 21]
        if len(raw) != 20:
            raise ValueError("refusing: a tree object is cut short")
        if name in ("", ".", "..", ".git") or "/" in name or name in out:
            raise ValueError(f"refusing the tree entry name {name!r}")
        out[name] = (mode, raw.hex())
        i = nul + 21
    return out


def _subject(commit_bytes: bytes) -> str:
    _, _, message = commit_bytes.partition(b"\n\n")
    first = message.split(b"\n\n", 1)[0].decode("utf-8", errors="replace")
    subject = " ".join(line.strip() for line in first.splitlines())
    return "".join(c if c.isprintable() else "?" for c in subject)  # no terminal escapes from a commit message


def read_commit(repo, commit: str) -> tuple[str, dict]:
    """Walk from the typed commit; return (subject, {path: (blob id, executable, bytes)}) for the export set.

    Every commit, tree and blob read here was re-hashed. Nothing is read twice under one id.
    """
    if not HASH_RE.fullmatch(commit):
        raise ValueError("the commit must be 40 lowercase hex characters")
    repo, verified = _open(repo), {}
    try:
        kind = repo.git("cat-file", "-t", commit).decode().strip()
    except subprocess.CalledProcessError:
        kind = "missing"
    if kind != "commit":
        raise ValueError(f"{commit} is not a commit in this repository ({kind})")
    raw = _read_verified(repo, "commit", commit, verified)
    first = raw.split(b"\n", 1)[0]
    if not re.fullmatch(rb"tree [0-9a-f]{40}", first):
        raise ValueError(f"refusing: commit {commit} does not start with a tree line")
    root = _parse_tree(_read_verified(repo, "tree", first[5:].decode(), verified))
    files: dict[str, tuple[str, bool, bytes]] = {}

    def add_file(path, mode, oid):
        if mode not in FILE_MODES:
            raise ValueError(f"refusing {path}: only plain files are exported (mode {mode})")
        files[path] = (oid, FILE_MODES[mode], _read_verified(repo, "blob", oid, verified))

    def add_tree(prefix, oid):
        for name, (mode, child) in _parse_tree(_read_verified(repo, "tree", oid, verified)).items():
            path = f"{prefix}/{name}"
            if mode == TREE_MODE:
                add_tree(path, child)
            else:
                add_file(path, mode, child)

    for name in TRACKED:
        if name not in root:
            continue
        mode, oid = root[name]
        if name == "adk" and mode == TREE_MODE:
            add_tree(name, oid)
        else:
            add_file(name, mode, oid)  # a symlink, a gitlink or a tree where a file belongs is refused here
    for path, (oid, _x, _data) in files.items():
        if oid not in verified or verified[oid][0] != "blob":
            raise ValueError(f"refusing {path}: its blob {oid} was not verified")
    return _subject(raw), files


def check_commit(repo, commit: str) -> str:
    """Refuse an unsafe repository or a typed hash that is not a full commit hash in it; return the commit subject."""
    if not HASH_RE.fullmatch(commit):
        raise ValueError("the commit must be 40 lowercase hex characters")
    repo = _open(repo)
    refuse_unsafe_repo(repo)
    run_fsck(repo)
    return read_commit(repo, commit)[0]


def _checked(path, euid, what, root_ok=False):
    st = os.lstat(path)
    if st.st_uid != euid and not (root_ok and st.st_uid == 0):
        raise ValueError(f"{what} {path} is not owned by you")
    if st.st_mode & 0o022:
        raise ValueError(f"{what} {path} is writable by group or others")
    return st


def _trusted_files(repo, commit) -> dict:
    repo = _open(repo)
    refuse_unsafe_repo(repo)
    run_fsck(repo)
    files = read_commit(repo, commit)[1]
    for need in REQUIRED:
        if need not in files:
            raise ValueError(f"the commit is missing {need}")
    return files


def verify_export(repo, dest, commit) -> None:
    """Check that dest holds exactly the commit's files, and only the owner can change them.

    The commit's files come from the verified walk (read_commit), never from git's
    own listing. Every file in dest must have the verified blob id and exec bit, and the
    exact path set must match, so a file that is missing, extra or changed is refused.
    """
    want = {p: (oid, x) for p, (oid, x, _d) in _trusted_files(repo, commit).items()}
    euid, dest = os.geteuid(), os.fspath(dest)
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


def _write(dest: str, files: dict) -> None:
    os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
    os.mkdir(dest, 0o700)
    for path in sorted(files):
        _oid, executable, data = files[path]
        target = os.path.join(dest, *path.split("/"))
        folder = dest
        for part in path.split("/")[:-1]:
            folder = os.path.join(folder, part)
            if not os.path.isdir(folder):
                os.mkdir(folder, 0o755)
                os.chmod(folder, 0o755)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            os.fchmod(f.fileno(), 0o755 if executable else 0o644)


def export_commit(repo, dest, commit) -> str:
    """Replace dest with the copy of adk/ and install.sh in commit, written from verified bytes; return the hash."""
    files = _trusted_files(repo, commit)  # refuses before dest is touched
    dest = os.fspath(dest)
    shutil.rmtree(dest, ignore_errors=True)
    try:
        _write(dest, files)
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
    except (subprocess.CalledProcessError, OSError, ValueError) as err:
        print(f"export failed: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
