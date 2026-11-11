"""PreToolUse hook: browser-control and computer-use tools are off unless switched on."""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adk.hooks.common import deny, run
from adk.paths import Paths
from adk.state import load_lists

PREFIXES: tuple[tuple[str, str], ...] = (
    ("mcp__claude-in-chrome__", "chrome"),
    ("mcp__computer-use__", "computer-use"),
)


def _integration_of(tool) -> str | None:
    if not isinstance(tool, str):
        return None
    for prefix, name in PREFIXES:
        if tool.startswith(prefix):
            return name
    return None


def _nonempty_str(v) -> str | None:
    return v if isinstance(v, str) and v else None


def decide(event: dict, p: Paths) -> dict | None:
    name = _integration_of(event.get("tool_name"))
    if name is None:
        return None
    raw = _nonempty_str(event.get("_project_dir")) or _nonempty_str(event.get("cwd"))
    if raw is None:
        return deny("adk: project directory missing from hook input")

    project = Path(raw).expanduser().resolve()
    listed = load_lists(p)["integrations"]
    for d in (project, *project.parents):
        for key, names in listed.items():
            if Path(key).expanduser().resolve() == d and isinstance(names, list) and name in names:
                return None
    return deny(
        f"adk: {name} is off for this project. Eero can switch it on with "
        f"`adk integration on {name}`."
    )


if __name__ == "__main__":
    sys.exit(run(decide))
