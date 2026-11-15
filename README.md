# agentic-development-kit

adk is a personal Claude Code kit: a standard-library Python package with an `adk` command line entry point (`python3 -m adk`) and plugin manifests under `.claude-plugin/`.

Claude runs the fast tests inside its sandbox with `python3 -W error -m unittest discover -s tests -v`. Do not run them from your own terminal in this folder: Claude can write to this folder, so that would run code Claude may have changed. To test a reviewed commit yourself, run them from a fresh export of that commit under `~/.config/adk`, never from this folder.

## Operating the wall

The operator guide is in [docs/operating-the-wall.md](docs/operating-the-wall.md). It covers what the wall closes, install and uninstall, and what to do when a tool is blocked.

Commands (run as `/usr/local/bin/adk <command>`, or from the export as `(cd ~/.config/adk/src && /usr/bin/python3 -B -E -s -m adk <command>)`; never run kit code from the repo folder):

- `status`: show allowed, never-allowed and re-opened folders, and integrations on for a project.
- `allow-dir DIR` / `never-dir DIR`: add a folder to the allow or never list (`--remove` undoes it).
- `reopen DIR`: re-open a closed tool folder (`--write` also allows writes, `--remove` closes it again).
- `integration on|off chrome|computer-use`: switch an integration on or off for one project (`--project DIR`).
- `install plan|apply-user|restore-user`: used by `install.sh`; you normally do not run these yourself.
- `wall-test`: run the wall test (two short headless Haiku sessions, so it uses quota).
- `doctor`: check the wall is installed and working (`--quick` skips the wall test).

Install with `install.sh <commit-hash>` and remove with `install.sh uninstall <commit-hash>`, both from a terminal and only from the verified export of the reviewed commit under `~/.config/adk`. See `docs/operating-the-wall.md`.
