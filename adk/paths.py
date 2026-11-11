"""Where the kit's files live on the machine."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    home: Path
    root: Path
    claude_user_settings: Path
    config_dir: Path
    config_file: Path
    secrets_file: Path
    lists_file: Path
    canary_dir: Path
    backups_dir: Path
    managed_dir: Path
    managed_dropin: Path
    lib_dir: Path
    bin_link: Path
    apparmor_profile: Path
    root_backups_dir: Path


def resolve(home: Path | None = None, root: Path = Path("/")) -> Paths:
    home = Path.home() if home is None else Path(home)
    root = Path(root)
    config_dir = home / ".config" / "adk"
    managed_dir = root / "etc" / "claude-code"
    return Paths(
        home=home,
        root=root,
        claude_user_settings=home / ".claude" / "settings.json",
        config_dir=config_dir,
        config_file=config_dir / "config.json",
        secrets_file=config_dir / "secrets.env",
        lists_file=config_dir / "lists.json",
        canary_dir=config_dir / "canary",
        backups_dir=config_dir / "backups",
        managed_dir=managed_dir,
        managed_dropin=managed_dir / "managed-settings.d" / "50-adk-wall.json",
        lib_dir=root / "usr" / "local" / "lib" / "agentic-dev-kit",
        bin_link=root / "usr" / "local" / "bin" / "adk",
        apparmor_profile=root / "etc" / "apparmor.d" / "bwrap",
        root_backups_dir=root / "var" / "backups" / "agentic-dev-kit",
    )
