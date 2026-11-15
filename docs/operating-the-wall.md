# Operating the wall

This guide is for Eero. It explains what the wall does, how to install and remove it, and what to do when it gets in the way.

Words you may not know:

- **Sandbox**: a restricted box that Claude's shell commands run in. It limits which files and network addresses a command can reach.
- **Wall**: the whole set of protections: the sandbox, the deny rules and the hooks, all set in managed settings.
- **Managed settings**: settings files under `/etc` that Claude Code reads first and that a project cannot override.
- **Hook**: a small script that Claude Code runs before some actions and that can stop them.
- **Headless session**: a Claude session with no window, started from a script (`claude -p`).

## Which `adk` to run

**Never run kit code from the repo folder.** From `/home/eero/projects/agentic-dev-kit`, `python3 -m adk` loads the `./adk` folder that Claude can write, and that code would run as you, outside the sandbox.

So this guide never uses `python3 -m adk`. Every kit command is written `/usr/local/bin/adk <command>`, the launcher the installer puts under `/usr/local/lib/agentic-dev-kit`, which only root can change. Before the first install, and when the wall blocks everything, use the exported copy instead:

```
(cd ~/.config/adk/src && /usr/bin/python3 -B -E -s -m adk <command>)
```

`~/.config/adk/src` is written by the installer from the commit you typed. Claude cannot reach it. In both forms, run the command from any folder except the repo.

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

Get the hash of the commit you reviewed with `git -C ~/projects/agentic-dev-kit log -1 --format=%H <branch>` or from `git log`, and read what it changes. Check the hash against the log by eye, because the first run (below) trusts the repository at that path. Then run, with `<hash>` replaced by the full hash:

```
export GIT_NO_REPLACE_OBJECTS=1; H=<hash>; R=${ADK_REPO:-$HOME/projects/agentic-dev-kit}; D=$HOME/.config/adk/install-$H
git -C "$R" fsck --no-dangling --no-progress && rm -rf "$D" && mkdir -p -m 700 "$D" && git -C "$R" cat-file blob "$H:adk/install/export.py" >"$D/export.py" && /usr/bin/python3 -I -B "$D/export.py" export "$R" "$D/src" "$H" && "$D/src/install.sh" "$H"
```

`install.sh` without a hash, or with anything that is not 40 lowercase hex characters, prints this usage and exits with code 2.

What the line does, and what it trusts:

1. `git fsck --no-dangling` makes git check the hash of every object in the repository. Git does not do that when it reads a file, and Claude can write into `.git/objects`. Any complaint stops the line.
2. It copies `export.py` out of the commit into `~/.config/adk/install-<hash>/`, a new folder only you can write. This is the first run, and it is the one weak spot: that copy is only as honest as the repository at that path. The fsck in step 1 is what covers it. The hash you typed is what you check by eye.
3. `export.py` walks from the typed commit, re-reads every commit, tree and file it uses, recomputes each hash itself and refuses on any difference. It writes `adk/` and `install.sh` from those verified bytes to `~/.config/adk/install-<hash>/src`. It does not use `git archive`, `.gitattributes` or `.git/info/attributes`.
4. It runs `install.sh` from `src`. `install.sh` refuses to run from any other place, including the repo.

Both checks rest on SHA-1: a forged file with the same SHA-1 as a reviewed one would pass. Making one needs a SHA-1 collision with a chosen target, which a file write alone does not give. That risk is not closed here.

The one-liner uses `~/.config/adk`, never a temp folder, because a temp folder is a place Claude can write when you run a command with `!` inside a session. Run the install from a plain terminal anyway.

The installer will:

1. Refuse to run from anywhere but `~/.config/adk/install-<hash>/src`, and check that this folder is exactly the export of the commit.
2. Refuse if the repository has any `refs/replace` entry (`git replace`), a `commondir`, `objects/info/alternates`, `info/grafts` or `shallow` file, or if `git fsck --no-dangling` says anything. These make git show other file contents under the same hash, or read objects from another folder.
3. Refuse if the hash is not a commit in the repository (it looks in the current folder's repository, in `$ADK_REPO`, then in `~/projects/agentic-dev-kit`).
4. Print the full hash and the commit subject, and ask `Install commit <hash> (<subject>)? [y/N]`. Only `y` continues. Nothing from the commit has run yet.
5. Export that commit's `adk/` and `install.sh` into `~/.config/adk/src`, a folder Claude cannot reach, the same verified way. It refuses if the exported files are not exactly the commit's files with the commit's contents, or if the folder is not owned by you or is writable by anyone else.
6. Show the plan and ask `Apply the plan above? [y/N]`. The plan, the user step, the root step and `adk doctor` all run from the exported copy. Before each root step the copy is checked again.
7. Ask for your sudo password. The root part copies files into `/etc` and reloads AppArmor.

## Uninstall and the rescue path

Use this when the wall blocks everything, including sessions that should start normally. The uninstall does not need Claude.

It also needs a commit hash, and it runs the root restore from that commit's export, the same way as the install. Use the hash of the commit you installed, or any reviewed commit. Run the same two lines as for the install, with `uninstall "$H"` in place of `"$H"` after the last `&&`:

```
export GIT_NO_REPLACE_OBJECTS=1; H=<hash>; R=${ADK_REPO:-$HOME/projects/agentic-dev-kit}; D=$HOME/.config/adk/install-$H
git -C "$R" fsck --no-dangling --no-progress && rm -rf "$D" && mkdir -p -m 700 "$D" && git -C "$R" cat-file blob "$H:adk/install/export.py" >"$D/export.py" && /usr/bin/python3 -I -B "$D/export.py" export "$R" "$D/src" "$H" && "$D/src/install.sh" uninstall "$H"
```

It shows the hash and subject, asks `Uninstall commit <hash> (<subject>)? [y/N]`, restores the files it backed up, removes the user-level changes, and reloads AppArmor. Run it from a plain terminal. It asks for your sudo password.

After uninstalling, run `(cd ~/.config/adk/src && /usr/bin/python3 -B -E -s -m adk doctor)` to check the state. The launcher `/usr/local/bin/adk` is gone after an uninstall. If you want the wall back, install a reviewed commit again.

## Running a command inside a Claude session: the `!` prefix

Some kit commands change your own lists (allowed folders, integrations). Claude is not allowed to run them. You can run them yourself, from inside a Claude session, by typing `!` before the command, for example:

```
! /usr/local/bin/adk status
```

A command with `!` runs as you, outside the sandbox. Claude cannot type the `!` for you. Whether this works from the phone is not promised yet.

## The `adk` commands

Every command is `/usr/local/bin/adk <command>` (see "Which `adk` to run" above). Run `/usr/local/bin/adk --help` for the list, and `/usr/local/bin/adk <command> --help` for the options.

- **`status`** lists the allowed folders, the never-allowed folders, the re-opened folders, and the integrations on for a project. Without `--project` it uses the current folder.
  ```
  /usr/local/bin/adk status --project ~/projects/my-app
  ```
- **`allow-dir DIR`** adds a folder to your list of folders Claude may use. `--remove` takes it off the list.
  ```
  /usr/local/bin/adk allow-dir ~/projects/shared-tools
  ```
- **`never-dir DIR`** adds a folder to your never-allow list. `--remove` takes it off.
  ```
  /usr/local/bin/adk never-dir ~/projects/private-notes
  ```
- **`reopen DIR`** re-opens a common tool folder (for example a Node or Git setup) so a tool can read it. Add `--write` to allow writes too. `--remove` closes it again.
  ```
  /usr/local/bin/adk reopen ~/.nvm --write
  ```
- **`integration on|off`** switches Claude in Chrome or computer use on or off for one project. Both are off by default.
  ```
  /usr/local/bin/adk integration on chrome --project ~/projects/my-app
  ```
- **`install`** has three parts: `plan`, `apply-user` and `restore-user`. You normally do not run them yourself. `install.sh <hash>` runs `plan` and `apply-user`, and `install.sh uninstall <hash>` runs `restore-user`, both from the exported copy.
  ```
  /usr/local/bin/adk install plan
  ```
- **`wall-test`** runs the wall test. See the section below.
  ```
  /usr/local/bin/adk wall-test
  ```
- **`doctor`** checks that the wall is installed and working. `--quick` skips the wall test.
  ```
  /usr/local/bin/adk doctor --quick
  ```

## When a tool fails because its folder is closed

Sometimes a tool fails with "Permission denied" or "file does not exist" even though the file is there. The folder is probably closed to Claude.

1. Run `/usr/local/bin/adk status --project <your project>` to see what is closed.
2. If the folder is a tool folder you trust (for example `~/.nvm` for Node), reopen it: `/usr/local/bin/adk reopen <folder>`. Add `--write` only if the tool must write there.
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
/usr/local/bin/adk wall-test
```

The wall test attacks a throwaway project with planted fake secrets. It starts headless Claude sessions and checks that the secrets cannot be read or leaked, that forbidden writes fail, and that the sandbox is on. It prints each check as ok or failed, and exits 0 only when every check passes.

**Cost in quota.** Each run starts two short headless sessions on the Haiku model. Those sessions use your Claude usage. The cost is small, but it is not zero. The test does not use an API key: the wall removes `ANTHROPIC_API_KEY` from the environment so the runs cannot silently bill the API. Run the test when you install, after a change to the wall, and at each release. You do not need it every day. `adk doctor --quick` skips it.

Options:

- `--settings FILE` tests a candidate settings file instead of the installed policy.
- `--installed` tests whatever the machine has installed.
- `--keep` keeps the throwaway project and the generated settings, so you can look at them.

If a check fails, do not install or keep a change until you understand why. Read `docs/wall-findings.md` first.
