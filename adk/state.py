"""User-adjustable lists: allowed, never-allowed and re-opened folders, integrations."""
from __future__ import annotations

from pathlib import Path

from adk.jsonio import InvalidJson, read_json, write_json_atomic
from adk.paths import Paths

INTEGRATIONS = {"chrome", "computer-use"}

__all__ = [
    "InvalidJson", "load_lists", "add_never_dir", "remove_never_dir",
    "set_integration", "integrations_for", "allowed_dirs", "add_allowed_dir",
    "remove_allowed_dir", "reopened", "add_reopened", "remove_reopened",
]


def _res(d) -> str:
    return str(Path(d).expanduser().resolve())


def load_lists(p: Paths) -> dict:
    data = read_json(p.lists_file, {})
    if not isinstance(data, dict):
        data = {}
    never = data.get("never_dirs")
    integ = data.get("integrations")
    return {
        "never_dirs": list(never) if isinstance(never, list) else [],
        "integrations": dict(integ) if isinstance(integ, dict) else {},
    }


def _save_lists(p: Paths, lists: dict) -> None:
    data = read_json(p.lists_file, {})
    if not isinstance(data, dict):
        data = {}
    data.update(lists)
    write_json_atomic(p.lists_file, data, 0o600)


def _get_list(settings: dict, *keys: str) -> list:
    node = settings
    for k in keys:
        node = node.get(k) if isinstance(node, dict) else None
    return list(node) if isinstance(node, list) else []


def _set_list(settings: dict, keys: tuple, value: list) -> None:
    node = settings
    for k in keys[:-1]:
        child = node.get(k)
        if not isinstance(child, dict):
            child = {}
            node[k] = child
        node = child
    node[keys[-1]] = value


_ALLOW = ("permissions", "additionalDirectories")
_READ = ("sandbox", "filesystem", "allowRead")
_WRITE = ("sandbox", "filesystem", "allowWrite")


def _edit_settings(p: Paths, changes: list) -> bool:
    """changes: (keys, add?, value). Applies them; writes only if something changed."""
    settings = read_json(p.claude_user_settings, {})
    if not isinstance(settings, dict):
        raise InvalidJson(f"{p.claude_user_settings}: expected a JSON object")
    changed = False
    for keys, add, value in changes:
        cur = _get_list(settings, *keys)
        if add and value not in cur:
            cur.append(value)
        elif not add and value in cur:
            cur = [x for x in cur if x != value]
        else:
            continue
        _set_list(settings, keys, cur)
        changed = True
    if changed:
        write_json_atomic(p.claude_user_settings, settings, 0o600)
    return changed


# never list

def add_never_dir(p: Paths, d: Path) -> bool:
    s = _res(d)
    # Validate settings first so a bad file leaves everything untouched.
    _edit_settings(p, [(_ALLOW, False, s)])
    lists = load_lists(p)
    if s in lists["never_dirs"]:
        return False
    lists["never_dirs"].append(s)
    _save_lists(p, lists)
    return True


def remove_never_dir(p: Paths, d: Path) -> bool:
    s = _res(d)
    lists = load_lists(p)
    if s not in lists["never_dirs"]:
        return False
    lists["never_dirs"] = [x for x in lists["never_dirs"] if x != s]
    _save_lists(p, lists)
    return True


# integrations

def set_integration(p: Paths, project: Path, name: str, on: bool) -> bool:
    if name not in INTEGRATIONS:
        raise ValueError(f"unknown integration {name!r}; expected one of {sorted(INTEGRATIONS)}")
    key = _res(project)
    lists = load_lists(p)
    cur = list(lists["integrations"].get(key, []))
    if on and name not in cur:
        cur.append(name)
    elif not on and name in cur:
        cur.remove(name)
    else:
        return False
    lists["integrations"][key] = cur
    _save_lists(p, lists)
    return True


def integrations_for(p: Paths, project: Path) -> list[str]:
    return list(load_lists(p)["integrations"].get(_res(project), []))


# allow list

def allowed_dirs(p: Paths) -> list[Path]:
    settings = read_json(p.claude_user_settings, {})
    if not isinstance(settings, dict):
        return []
    return [Path(x) for x in _get_list(settings, *_ALLOW)]


def add_allowed_dir(p: Paths, d: Path) -> bool:
    s = _res(d)
    changed = _edit_settings(p, [(_ALLOW, True, s)])
    lists = load_lists(p)
    if s in lists["never_dirs"]:
        lists["never_dirs"] = [x for x in lists["never_dirs"] if x != s]
        _save_lists(p, lists)
    return changed


def remove_allowed_dir(p: Paths, d: Path) -> bool:
    return _edit_settings(p, [(_ALLOW, False, _res(d))])


# re-opened folders

def reopened(p: Paths) -> dict:
    settings = read_json(p.claude_user_settings, {})
    if not isinstance(settings, dict):
        settings = {}
    return {"read": _get_list(settings, *_READ), "write": _get_list(settings, *_WRITE)}


def add_reopened(p: Paths, d: Path, write: bool = False) -> bool:
    s = _res(d)
    changes = [(_READ, True, s)]
    if write:
        changes.append((_WRITE, True, s))
    return _edit_settings(p, changes)


def remove_reopened(p: Paths, d: Path) -> bool:
    s = _res(d)
    return _edit_settings(p, [(_READ, False, s), (_WRITE, False, s)])
