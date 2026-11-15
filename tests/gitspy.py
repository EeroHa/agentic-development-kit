"""A fake `git` for tests: it logs every call (arguments and the git config variables) and then runs the real git."""
import os
import shutil
from pathlib import Path


def install(folder: Path, log: Path) -> str:
    """Write the spy into folder and return a PATH value that finds it first."""
    folder.mkdir(parents=True, exist_ok=True)
    spy = folder / "git"
    spy.write_text('#!/bin/sh\necho "$*|nosystem=$GIT_CONFIG_NOSYSTEM|global=$GIT_CONFIG_GLOBAL" >>"%s"\nexec "%s" "$@"\n'
                   % (log, shutil.which("git")))
    spy.chmod(0o755)
    return f"{folder}:{os.environ['PATH']}"


def calls(log: Path) -> list:
    return log.read_text().splitlines() if log.exists() else []


def make_redirect_config(repo: Path, marker: Path) -> None:
    """The attack: .git/commondir names a folder whose config runs a command (a pager) that writes marker."""
    git = repo / ".git"
    (git / "evil").mkdir()
    (git / "evil" / "config").write_text(
        f"[core]\n\tpager = touch {marker}\n\tfsmonitor = touch {marker}\n[pager]\n\tfsck = touch {marker}\n\tlog = touch {marker}\n")
    (git / "commondir").write_text("evil\n")
