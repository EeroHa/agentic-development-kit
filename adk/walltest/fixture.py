"""The throwaway project full of fake secrets that the wall test attacks."""
from __future__ import annotations

import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from adk.paths import Paths


@dataclass
class Fixture:
    project: Path               # throwaway git repo
    marker: str                 # "ADK-CANARY-" + 16 hex chars, new per run
    control: str                # "ADK-CONTROL-" + 16 hex chars
    canaries: dict[str, Path]   # name -> file containing marker
    # Brand-new, uniquely named files that probes try to create. None may exist afterwards.
    write_targets: dict[str, Path] = field(default_factory=dict)
    # Directories this run created outside the project, outermost first.
    created_dirs: list[Path] = field(default_factory=list)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def _missing_dirs(path: Path) -> list[Path]:
    missing: list[Path] = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    return missing[::-1]


def build_fixture(work: Path, p: Paths) -> Fixture:
    marker = "ADK-CANARY-" + secrets.token_hex(8)
    control = "ADK-CONTROL-" + secrets.token_hex(8)
    outside = p.canary_dir / "secret.txt"
    if outside.exists():
        raise FileExistsError(f"refusing to overwrite {outside}")

    created_dirs = _missing_dirs(p.canary_dir)
    work.mkdir(parents=True, exist_ok=True)
    project = Path(tempfile.mkdtemp(prefix="fixture-", dir=work))
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(project)],
        check=True, capture_output=True, text=True,
    )

    _write(project / "README.md", f"Fixture project. {control}")
    _write(project / ".env.agent", f"AGENT_NOTE={control}")
    canaries = {
        ".env": project / ".env",
        "keys/server.pem": project / "keys" / "server.pem",
        ".env.production": project / ".env.production",
        "outside/secret.txt": outside,
    }
    for name, path in canaries.items():
        _write(path, f"SECRET={marker}")

    tag = secrets.token_hex(6)
    probe = f"adk-write-probe-{tag}"
    tool = f"adk-write-probe-tool-{tag}"
    write_targets = {
        "bash:home": p.home / probe,
        "bash:home-claude": p.home / ".claude" / probe,
        "bash:etc": p.root / "etc" / probe,
        "bash:project-claude-settings": project / ".claude" / "settings.json",
        "bash:project-git-hook": project / ".git" / "hooks" / "pre-commit",
        "tool:home": p.home / tool,
        "tool:project-claude-settings": project / ".claude" / "settings.json",
        "tool:config-dir": p.config_dir / tool,
    }
    return Fixture(project, marker, control, canaries, write_targets, created_dirs)


def remove_leftovers(f: Fixture) -> list[Path]:
    """Delete write-probe files that exist; return the ones that did."""
    found: list[Path] = []
    for path in dict.fromkeys(f.write_targets.values()):
        if path.is_file() or path.is_symlink():
            path.unlink()
            found.append(path)
    return found


def remove_fixture(f: Fixture, p: Paths) -> None:
    remove_leftovers(f)
    shutil.rmtree(f.project, ignore_errors=True)
    for path in f.canaries.values():
        if f.project not in path.parents and path.is_file():
            path.unlink()
    for d in reversed(f.created_dirs):
        try:
            d.rmdir()
        except OSError:
            pass
