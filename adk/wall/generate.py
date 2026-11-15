"""Turn the wall policy into a Claude Code managed-settings document."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Iterable

from adk.paths import Paths

EDIT_DENY: tuple[str, ...] = (
    "Edit(//**/.claude/settings.json)",
    "Edit(//**/.claude/settings.local.json)",
    "Edit(//**/.mcp.json)",
    "Edit(//**/.git/hooks/**)",
    "Edit(//**/.git/config)",
    "Edit(~/.config/adk/**)",
    # Git runs these without asking: a submodule's or worktree's config and hooks.
    "Edit(//**/.git/modules/**/config*)",
    "Edit(//**/.git/modules/**/hooks/**)",
    "Edit(//**/.git/worktrees/*/config*)",
    "Edit(//**/.git/worktrees/**/hooks/**)",
    # Programs that run unsandboxed: shell profiles, startup items, user services,
    # ~/.local/bin (ahead of /usr/bin on PATH) and the installed plugins.
    "Edit(~/.bashrc)",
    "Edit(~/.bash_profile)",
    "Edit(~/.profile)",
    "Edit(~/.zshrc)",
    "Edit(~/.config/autostart/**)",
    "Edit(~/.config/systemd/user/**)",
    "Edit(~/.local/bin/**)",
    "Edit(~/.claude/plugins/**)",
)


def load_policy(path: Path | None = None) -> dict:
    if path is None:
        path = Path(__file__).with_name("policy.json")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def credential_like(names: Iterable[str], policy: dict) -> list[str]:
    patterns = [s.lower() for s in policy["env_name_patterns"]]
    return sorted({n for n in names if any(s in n.lower() for s in patterns)})


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _read_rules_for_dir(entry: str) -> list[str]:
    rules = [f"Read({entry})"]
    if not PurePosixPath(entry).suffix:
        rules.append(f"Read({entry}/**)")
    return rules


def _hook(p: Paths, script: str) -> dict:
    return {
        "type": "command",
        "command": "/usr/bin/python3",
        "args": ["-I", str(p.lib_dir / "adk" / "hooks" / script)],
        "timeout": 10,
        "onFailure": "block",
    }


def build_managed_settings(policy: dict, p: Paths, env_names: Iterable[str]) -> dict:
    secret_dirs = policy["secret_dirs"]
    secret_files = policy["secret_file_names"]

    denied = (set(policy["env_deny"]) | set(credential_like(env_names, policy))) - set(
        policy["env_never_deny"]
    )

    deny_read = _unique(
        ["~/", *secret_dirs, *(f"~/**/{name}" for name in secret_files)]
    )
    deny_rules: list[str] = [f"Read(//**/{name})" for name in secret_files]
    for entry in secret_dirs:
        deny_rules.extend(_read_rules_for_dir(entry))
    deny_rules.extend(EDIT_DENY)

    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "filesystem": {
                "denyRead": deny_read,
                "allowRead": _unique(
                    [*policy["reopen_read"], *(f"~/**/{name}" for name in policy["readable_env_files"])]
                ),
                "allowWrite": _unique(policy["reopen_write"]),
            },
            "credentials": {
                "envVars": [{"name": n, "mode": "deny"} for n in sorted(denied)],
            },
            "network": {"allowedDomains": _unique(policy["allowed_domains"])},
        },
        "permissions": {
            "deny": _unique(deny_rules),
            "blockReadsOutsideWorkingDirectories": True,
            "disableBypassPermissionsMode": "disable",
        },
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Edit|Write|NotebookEdit",
                    "hooks": [_hook(p, "outside_write_guard.py")],
                },
                {
                    "matcher": "mcp__(claude-in-chrome|computer-use)__.*",
                    "hooks": [_hook(p, "integration_guard.py")],
                },
            ],
        },
        "strictKnownMarketplaces": list(policy["marketplaces"]),
    }
