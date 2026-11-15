"""Export the committed kit out of the working tree, so the installer runs reviewed code.

Standard library only. install.sh runs the committed copy of this file:
    git show HEAD:adk/install/export.py | /usr/bin/python3 -I - <repo> <dest>
"""
import io
import os
import shutil
import subprocess
import sys
import tarfile

TRACKED = ("adk", "install.sh")


def _git(repo, *args):
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    return done.stdout


def dirty_paths(repo) -> list[str]:
    """Paths under adk/ or install.sh that differ from HEAD, or are hidden from git status."""
    found = _git(repo, "status", "--porcelain", "--", *TRACKED).decode().splitlines()
    # skip-worktree ("S") and assume-unchanged (lowercase tag) hide edits from status.
    for line in _git(repo, "ls-files", "-v", "--", *TRACKED).decode().splitlines():
        if line[:1] == "S" or line[:1].islower():
            found.append(f"hidden from git status: {line[2:]}")
    return found


def export_head(repo, dest) -> str:
    """Replace dest with the HEAD copy of adk/ (and install.sh); return the full commit hash."""
    repo, dest = os.fspath(repo), os.fspath(dest)
    commit = _git(repo, "rev-parse", "--verify", "HEAD").decode().strip()
    archive = _git(repo, "archive", "--format=tar", commit, *TRACKED)
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")
    for need in ("adk/__init__.py", "adk/wall/policy.json"):
        if not os.path.isfile(os.path.join(dest, need)):
            shutil.rmtree(dest, ignore_errors=True)
            raise ValueError(f"the exported copy is missing {need}")
    return commit


def main(argv) -> int:
    if len(argv) != 2:
        print("usage: export.py <repo> <dest>", file=sys.stderr)
        return 2
    repo, dest = argv
    try:
        bad = dirty_paths(repo)
        if bad:
            print("Uncommitted changes under adk/ or in install.sh. Review and commit them first:", file=sys.stderr)
            print("\n".join(bad), file=sys.stderr)
            return 1
        commit = export_head(repo, dest)
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").decode().strip()
    except (subprocess.CalledProcessError, OSError, ValueError, tarfile.TarError) as err:
        print(f"export failed: {err}", file=sys.stderr)
        return 1
    print(f"{commit} {branch}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
