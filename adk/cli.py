"""Command line entry point for adk.

Subcommands are registered by modules listed in _COMMAND_MODULES; each such
module calls register(...) when imported.
"""

import argparse
import importlib
from typing import Callable

from adk import __version__

_COMMAND_MODULES: list[str] = ["adk.commands"]

_REGISTRY: dict[str, tuple[Callable[[argparse.ArgumentParser], None], Callable[[argparse.Namespace], int]]] = {}


def register(
    name: str,
    build: Callable[[argparse.ArgumentParser], None],
    run: Callable[[argparse.Namespace], int],
) -> None:
    _REGISTRY[name] = (build, run)


def main(argv: list[str] | None = None) -> int:
    for module_name in _COMMAND_MODULES:
        importlib.import_module(module_name)

    parser = argparse.ArgumentParser(prog="adk")
    parser.add_argument("--version", action="version", version=f"adk {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    for name, (build, _run) in _REGISTRY.items():
        sub = subparsers.add_parser(name)
        build(sub)

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return _REGISTRY[args.command][1](args)
