# agentic-development-kit

adk is a personal Claude Code kit: a standard-library Python package with an `adk` command line entry point (`python3 -m adk`) and plugin manifests under `.claude-plugin/`.

Run the fast tests with `python3 -m unittest discover -s tests -v`. Run the plugin check with `claude plugin validate . --strict`.

## Operating the wall

The operator guide is in [docs/operating-the-wall.md](docs/operating-the-wall.md). It covers what the wall closes, install and uninstall, and what to do when a tool is blocked.

Commands (run as `python3 -m adk <command>`):

- `status`: show allowed, never-allowed and re-opened folders, and integrations on for a project.
- `allow-dir DIR` / `never-dir DIR`: add a folder to the allow or never list (`--remove` undoes it).
- `reopen DIR`: re-open a closed tool folder (`--write` also allows writes, `--remove` closes it again).
- `integration on|off chrome|computer-use`: switch an integration on or off for one project (`--project DIR`).
- `install plan|apply-user|restore-user`: used by `install.sh`; you normally do not run these yourself.
- `wall-test`: run the wall test (two short headless Haiku sessions, so it uses quota).
- `doctor`: check the wall is installed and working (`--quick` skips the wall test).

Install with `install.sh <commit-hash>` and remove with `install.sh uninstall <commit-hash>`, both from a terminal and only from the extracted copy of the reviewed commit. See `docs/operating-the-wall.md`.
