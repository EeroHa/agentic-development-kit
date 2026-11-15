# Operating the wall

This guide is for Eero. It explains what the wall does, how to install and remove it, and what to do when it gets in the way.

Words you may not know:

- **Sandbox**: a restricted box that Claude's shell commands run in. It limits which files and network addresses a command can reach.
- **Wall**: the whole set of protections: the sandbox, the deny rules and the hooks, all set in managed settings.
- **Managed settings**: settings files under `/etc` that Claude Code reads first and that a project cannot override.
- **Hook**: a small script that Claude Code runs before some actions and that can stop them.
- **Headless session**: a Claude session with no window, started from a script (`claude -p`).

Run every command in this guide from the repo root, `/home/eero/projects/agentic-dev-kit`, unless it says otherwise.

## What the wall closes

Inside the wall, Claude works without asking. The wall is the protection, not the prompts. These things are closed:

- **Your home folder.** Claude's commands see an empty home folder. Only the project folder is visible.
- **Secret-looking files.** Files named `.env`, `.env.*` (including `.env.example`), `.envrc`, `*.env`, `*.tfvars`, `.dev.vars`, `id_rsa`, `*.pem`, `*.key`, `*.p12` and similar cannot be read, by a shell command or by the file tools. The one exception is a file named `agent.env`: shell commands can read it, the file tools cannot. Put values Claude may see there.
- **Your `~/.config` and `~/.ssh` folders.** Shell commands cannot see them.
- **Secret environment variables.** Variables that look like credentials are removed from everything Claude runs. Your SSH agent and D-Bus session are not reachable.
- **Folders outside the project.** The file tools refuse to read them. Writes outside the project need your approval.
- **Claude's own settings.** Claude cannot change the project's `.claude/settings.json`, the git hooks, or the wall itself.
- **Most of the network.** Only a short list of addresses is allowed (package registries and similar). Other sites are refused.
- **Integrations.** Claude in Chrome and computer use are off. You switch them on per project with `adk integration`.

## What the wall does not cover

Read this part too. The wall is against Claude. It is not a guarantee against everything.

- **Code you run yourself.** Anything Claude writes and you run outside a Claude session runs with your full access. Review it before you run it.
- **Secrets you paste into a prompt.** They become part of the conversation. Do not paste keys.
- **Projects that use real keys.** Any process Claude starts can print its own environment. If a project gives its apps real keys, Claude could see those keys. The default is fake values only.
- **You.** You have root. You can always change the managed settings. The wall is not built against you.
- **Connectors and integrations.** Claude.ai connectors act with the access you gave them. They are not inside the sandbox.
- **Things on your machine outside Claude.** The wall does not stop you or other programs from using your files.
- **Where you start Claude.** Start Claude in a project folder, not in your home folder or in `~/projects`. The folder you start in is the one place the wall leaves open: Claude can read and write all of it. If you start in your home folder or in `~/projects`, everything inside is open, and only the list of secret-looking names protects your files, for shell commands and for the file tools alike.

## Install

Install from a terminal, not from inside a Claude session.

The installer installs only a commit you name. You type its full 40-character hash, after you have reviewed that commit. Nothing from the working tree is ever copied to a system folder or run as root, because Claude Code can write to the working tree.

Get the hash of the commit you reviewed with `git -C ~/projects/agentic-dev-kit log -1 --format=%H <branch>` or from `git log`, and read what it changes. Then run the copy of `install.sh` that belongs to that commit, not the working-tree copy:

```
export TMPDIR=${TMPDIR:-/tmp}
rm -rf "$TMPDIR/adk-install" && mkdir -m 700 "$TMPDIR/adk-install"
git -C ~/projects/agentic-dev-kit archive <hash> install.sh adk | tar -x -C "$TMPDIR/adk-install" && "$TMPDIR/adk-install/install.sh" <hash>
```

Replace `<hash>` with the full hash in both places. `install.sh` without a hash, or with anything that is not 40 lowercase hex characters, prints this usage and exits with code 2.

Why the extracted copy: `install.sh` itself lives in the working tree, and an edited copy could skip every check. At its start `install.sh` compares its own bytes and `adk/install/export.py` with the copies in the commit and refuses on any difference or if it cannot check. That is a tripwire, not a guarantee: a changed script can simply leave the check out. Only the extracted copy is safe to run.

The installer will:

1. Refuse to run if the repository has any `refs/replace` entry (`git replace`). Such an entry makes git show other file contents under the same hash.
2. Refuse if the hash is not a commit in the repository (it looks in the current folder's repository, in `$ADK_REPO`, then in `~/projects/agentic-dev-kit`).
3. Print the full hash and the commit subject, and ask `Install commit <hash> (<subject>)? [y/N]`. Only `y` continues. Nothing from the commit has run yet.
4. Export that commit's `adk/` and `install.sh` into `~/.config/adk/src`, a folder Claude cannot reach. It refuses if the exported files are not exactly the commit's files with the commit's contents (this catches `export-ignore` rules that silently drop files), or if the folder is not owned by you or is writable by anyone else.
5. Show the plan and ask `Apply the plan above? [y/N]`. The plan, the user step, the root step and `adk doctor` all run from the exported copy. Before each root step the copy is checked again.
6. Ask for your sudo password. The root part copies files into `/etc` and reloads AppArmor.

## Uninstall and the rescue path

Use this when the wall blocks everything, including sessions that should start normally. The uninstall does not need Claude.

It also needs a commit hash, and it runs the root restore from that commit's export, the same way as the install. Use the hash of the commit you installed, or any reviewed commit:

```
export TMPDIR=${TMPDIR:-/tmp}
rm -rf "$TMPDIR/adk-install" && mkdir -m 700 "$TMPDIR/adk-install"
git -C ~/projects/agentic-dev-kit archive <hash> install.sh adk | tar -x -C "$TMPDIR/adk-install" && "$TMPDIR/adk-install/install.sh" uninstall <hash>
```

It shows the hash and subject, asks `Uninstall commit <hash> (<subject>)? [y/N]`, restores the files it backed up, removes the user-level changes, and reloads AppArmor. Run it from a plain terminal. It asks for your sudo password.

After uninstalling, run `python3 -m adk doctor` to check the state. If you want the wall back, install a reviewed commit again.

## Running a command inside a Claude session: the `!` prefix

Some kit commands change your own lists (allowed folders, integrations). Claude is not allowed to run them. You can run them yourself, from inside a Claude session, by typing `!` before the command, for example:

```
! python3 -m adk status
```

A command with `!` runs as you, outside the sandbox. Claude cannot type the `!` for you. Whether this works from the phone is not promised yet.

## The `adk` commands

Every command is `python3 -m adk <command>`. Run `python3 -m adk --help` for the list, and `python3 -m adk <command> --help` for the options.

- **`status`** lists the allowed folders, the never-allowed folders, the re-opened folders, and the integrations on for a project. Without `--project` it uses the current folder.
  ```
  python3 -m adk status --project ~/projects/my-app
  ```
- **`allow-dir DIR`** adds a folder to your list of folders Claude may use. `--remove` takes it off the list.
  ```
  python3 -m adk allow-dir ~/projects/shared-tools
  ```
- **`never-dir DIR`** adds a folder to your never-allow list. `--remove` takes it off.
  ```
  python3 -m adk never-dir ~/projects/private-notes
  ```
- **`reopen DIR`** re-opens a common tool folder (for example a Node or Git setup) so a tool can read it. Add `--write` to allow writes too. `--remove` closes it again.
  ```
  python3 -m adk reopen ~/.nvm --write
  ```
- **`integration on|off`** switches Claude in Chrome or computer use on or off for one project. Both are off by default.
  ```
  python3 -m adk integration on chrome --project ~/projects/my-app
  ```
- **`install`** has three parts: `plan`, `apply-user` and `restore-user`. You normally do not run them yourself. `install.sh <hash>` runs `plan` and `apply-user`, and `install.sh uninstall <hash>` runs `restore-user`, both from the exported copy.
  ```
  python3 -m adk install plan
  ```
- **`wall-test`** runs the wall test. See the section below.
  ```
  python3 -m adk wall-test
  ```
- **`doctor`** checks that the wall is installed and working. `--quick` skips the wall test.
  ```
  python3 -m adk doctor --quick
  ```

## When a tool fails because its folder is closed

Sometimes a tool fails with "Permission denied" or "file does not exist" even though the file is there. The folder is probably closed to Claude.

1. Run `python3 -m adk status --project <your project>` to see what is closed.
2. If the folder is a tool folder you trust (for example `~/.nvm` for Node), reopen it: `python3 -m adk reopen <folder>`. Add `--write` only if the tool must write there.
3. If the tool needs a folder outside the project in general, use `allow-dir`. Use `never-dir` for folders that must stay closed.
4. Run either command with the `!` prefix inside a Claude session, or in a terminal.

Do not work around a closed folder by copying the files into the project. That defeats the wall.

Some folders are closed on purpose. `reopen` refuses your home folder itself and any folder that overlaps a secret location (for example `~/.config`). It cannot open those.

## Pushing code is manual for now

Claude cannot push to GitHub yet. The planned "GitHub door" (a bot account that can push branches but never `main`) is not built. Until it is, you push and open pull requests yourself:

```
git push -u origin <branch>
```

## Local ports: what does not work

The wall test measured this on 2026-11-15 (see `docs/wall-findings.md`, item 2):

- A server Claude starts and then connects to, inside one command, works.
- A server you start outside the sandbox, for example a local database, cannot be reached from Claude's commands. The connection is refused.
- A server started by one Bash command cannot be reached from the next Bash command in the same session.

So a database that runs in your Docker setup is not reachable from Claude's sessions yet. Plan around this. The verification design is meant to solve it later.

## The wall test

```
python3 -m adk wall-test
```

The wall test attacks a throwaway project with planted fake secrets. It starts headless Claude sessions and checks that the secrets cannot be read or leaked, that forbidden writes fail, and that the sandbox is on. It prints each check as ok or failed, and exits 0 only when every check passes.

**Cost in quota.** Each run starts two short headless sessions on the Haiku model. Those sessions use your Claude usage. The cost is small, but it is not zero. The test does not use an API key: the wall removes `ANTHROPIC_API_KEY` from the environment so the runs cannot silently bill the API. Run the test when you install, after a change to the wall, and at each release. You do not need it every day. `adk doctor --quick` skips it.

Options:

- `--settings FILE` tests a candidate settings file instead of the installed policy.
- `--installed` tests whatever the machine has installed.
- `--keep` keeps the throwaway project and the generated settings, so you can look at them.

If a check fails, do not install or keep a change until you understand why. Read `docs/wall-findings.md` first.
