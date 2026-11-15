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

Get the hash of the commit you reviewed from a terminal, with the repository's own `.git` checked first. `.git/commondir` lets git read its config and hooks from a folder Claude Code can write, and git runs that config (a pager, for example) as you, so test for it before any git command. This line prints nothing and runs no git if `commondir`, `info/grafts` or `shallow` exists:

```
R=${ADK_REPO:-$HOME/projects/agentic-dev-kit}
[ -z "$(ls -d "$R/.git/commondir" "$R/.git/info/grafts" "$R/.git/shallow" 2>/dev/null)" ] && GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git --git-dir="$R/.git" --no-pager -c core.pager=cat -c core.fsmonitor=false -c core.hooksPath=/dev/null log -1 --format=%H <branch>
```

Read what the commit changes, then check the hash against the log by eye, because the first run (below) trusts the repository at that path. Then paste this, with `<hash>` replaced by the full hash:

```
H=<hash>; R=${ADK_REPO:-$HOME/projects/agentic-dev-kit}; D=$HOME/.config/adk/install-$H
/usr/bin/python3 -I -B -E -s - "$R" "$D" "$H" <<"PY" && "$D/src/install.sh" "$H"
# bootstrap.py: the first verified read of the installer one-liner. Standard library only.
# Run as: /usr/bin/python3 -I -B -E -s bootstrap.py <repo> <dest> <40-hex-commit>
# Before any git command it refuses a .git that is not a plain folder, or that holds commondir,
# info/grafts, shallow, a non-empty alternates file or refs/replace entries: git would run config
# or hooks, or read objects, from a place Claude Code controls. Git then runs with no system or
# global config, no pager, no fsmonitor and no hooks.
# It reads the commit, its tree, the adk and adk/install trees and the blob adk/install/export.py
# through ONE `git cat-file --batch` process, each object once. It recomputes every object id as
# sha1("<type> <len>" + NUL + data) and refuses on any difference. Only then does it write
# those verified bytes to <dest>/export.py and run them: export.py export <repo> <dest>/src <commit>.
import hashlib, os, re, shutil, subprocess, sys

def refuse(why):
    sys.exit("bootstrap refused: " + why)

def tree_entries(data):
    out, i = {}, 0
    while i < len(data):
        sp = data.index(b" ", i)
        nul = data.index(b"\0", sp)
        name = data[sp + 1:nul]
        if name in out or len(data[nul + 1:nul + 21]) != 20:
            raise ValueError("a tree object is malformed")
        out[name] = (data[i:sp], data[nul + 1:nul + 21].hex())
        i = nul + 21
    return out

def refuse_redirects(git_dir):
    if os.path.islink(git_dir) or not os.path.isdir(git_dir):
        refuse(git_dir + " is not a plain folder")
    for rel in ("commondir", "info/grafts", "shallow"):
        if os.path.lexists(os.path.join(git_dir, rel)):
            refuse(rel + " exists in " + git_dir)
    alt = os.path.join(git_dir, "objects", "info", "alternates")
    if os.path.lexists(alt) and (os.path.islink(alt) or not os.path.isfile(alt) or os.path.getsize(alt) != 0):
        refuse(alt + " is not an empty file")
    replace = os.path.join(git_dir, "refs", "replace")
    if os.path.lexists(replace) and (os.path.islink(replace) or not os.path.isdir(replace) or os.listdir(replace)):
        refuse(replace + " has entries")
    try:
        with open(os.path.join(git_dir, "packed-refs"), "rb") as f:
            if b"refs/replace/" in f.read():
                refuse("packed-refs has refs/replace entries")
    except FileNotFoundError:
        pass

def main(repo, dest, commit):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        refuse("the commit must be 40 lowercase hex characters")
    git_dir = os.path.join(repo, ".git")
    refuse_redirects(git_dir)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_DIR=git_dir, GIT_NO_REPLACE_OBJECTS="1", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
               GIT_NO_LAZY_FETCH="1")
    cmd = ["git", "--no-pager", "-c", "core.pager=cat", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]
    try:
        session = subprocess.Popen(cmd + ["cat-file", "--batch"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, cwd=repo)
    except OSError as err:
        refuse("cannot start git: %s" % err)

    def read(kind, oid):
        session.stdin.write(oid.encode() + b"\n")
        session.stdin.flush()
        head = session.stdout.readline().split()
        if head[:2] != [oid.encode(), kind.encode()] or len(head) != 3 or not head[2].isdigit():
            refuse("cannot read %s %s" % (kind, oid))
        data = session.stdout.read(int(head[2]))
        if len(data) != int(head[2]) or session.stdout.read(1) != b"\n":
            refuse("%s %s is cut short" % (kind, oid))
        if hashlib.sha1(b"%s %d\0" % (kind.encode(), len(data)) + data).hexdigest() != oid:
            refuse("%s %s does not match its own hash (the object was replaced)" % (kind, oid))
        return data

    try:
        first = read("commit", commit).split(b"\n", 1)[0]
        if not re.fullmatch(rb"tree [0-9a-f]{40}", first):
            refuse("the commit does not start with a tree line")
        oid = first[5:].decode()
        for name, want in ((b"adk", b"40000"), (b"install", b"40000"), (b"export.py", None)):
            mode, oid = tree_entries(read("tree", oid)).get(name, (b"", ""))
            if mode != want and not (want is None and mode in (b"100644", b"100755")):
                refuse("%s is not a plain entry of the commit" % name.decode())
        code = read("blob", oid)
    except (ValueError, OSError) as err:
        refuse(str(err))
    finally:
        session.kill()
        session.wait()

    if os.path.basename(dest) != "install-" + commit or os.path.islink(dest) or os.path.islink(os.path.dirname(dest)):
        refuse("the destination must be a real folder named install-<hash>")
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
    os.mkdir(dest, 0o700)
    target = os.path.join(dest, "export.py")
    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as f:
        f.write(code)
    done = subprocess.run([sys.executable, "-I", "-B", "-E", "-s", target, "export", repo,
                           os.path.join(dest, "src"), commit], stdin=subprocess.DEVNULL)
    sys.exit(done.returncode)

if len(sys.argv) != 4:
    refuse("usage: bootstrap.py <repo> <dest> <commit>")
main(*sys.argv[1:])
PY
```

`install.sh` without a hash, or with anything that is not 40 lowercase hex characters, prints this usage and exits with code 2.

What the paste does, and what it trusts:

1. The first command is the Python code in the paste (the same text as `adk/install/bootstrap.py`; read it, because it is the first thing that runs). Before it starts any git command it refuses a `.git` that is not a plain folder, or that holds `commondir`, `info/grafts`, `shallow`, an `objects/info/alternates` that is not an empty file, or `refs/replace` entries. Then it starts one `git cat-file --batch`, with `--no-pager`, `core.pager=cat`, `core.fsmonitor=false`, `core.hooksPath=/dev/null` and no system or global git config, and reads through it the typed commit, its tree, the `adk` tree, the `adk/install` tree and the file `adk/install/export.py`, each object once. It recomputes every object's hash itself and refuses on any difference, so a swapped object cannot slip in between a check and a read. The code never uses `git show`, `git cat-file blob` or `git fsck` to get code.
2. It writes those verified bytes of `export.py` to `~/.config/adk/install-<hash>/`, a new folder only you can write, and runs them. This is the first run, and it is the one weak spot: git is the transport for the bytes, so the trust is in the hash you typed and in the pasted code, which you read.
3. `export.py` runs `git fsck --no-dangling`, then walks from the typed commit, re-reads every commit, tree and file it uses, recomputes each hash itself and refuses on any difference. It writes `adk/` and `install.sh` from those verified bytes to `~/.config/adk/install-<hash>/src`. It does not use `git archive`, `.gitattributes` or `.git/info/attributes`.
4. It runs `install.sh` from `src`. `install.sh` refuses to run from any other place, including the repo.

Both checks rest on SHA-1: a forged file with the same SHA-1 as a reviewed one would pass. Making one needs a SHA-1 collision with a chosen target, which a file write alone does not give. That risk is not closed here.

Git still reads the repository's own `.git/config`. The flags above switch off the keys that are known to run a program (pager, fsmonitor, hooks), and the system and global config are not read, but nothing here proves that no other key in `.git/config` can run code. Check that file by eye if you doubt the repository. The `git` program on `PATH` is trusted too.

The one-liner uses `~/.config/adk`, never a temp folder, because a temp folder is a place Claude can write when you run a command with `!` inside a session. Run the install from a plain terminal anyway.

The installer will:

1. Refuse to run from anywhere but `~/.config/adk/install-<hash>/src`, and check that this folder is exactly the export of the commit.
2. Refuse, before it runs any git command, if the repository's `.git` is not a plain folder, or holds a `commondir`, `info/grafts` or `shallow` file, an `objects/info/alternates` that is not an empty file (the sandbox leaves an empty one in the repo, which does nothing), or any `refs/replace` entry (`git replace`). Then run every git call with `--no-pager`, `core.pager=cat`, `core.fsmonitor=false`, `core.hooksPath=/dev/null` and no system or global git config, and refuse if `git fsck --no-dangling` says anything. `commondir` makes git read its config and hooks from a folder Claude can write; the others make git show other file contents under the same hash, or read objects from another folder.
3. Use one fixed repository, `$ADK_REPO` if set, else `~/projects/agentic-dev-kit`, the same one the one-liner reads. It never uses the repository of the folder you are standing in. It refuses if that path is not a git repository, or if the hash is not a commit in it.
4. Print the full hash and the commit subject, and ask `Install commit <hash> (<subject>)? [y/N]`. Only `y` continues. The hash you typed is the approval, not this prompt: the commit's `export.py` and `install.sh` have already run to get here, as you, and the prompt only catches a typo.
5. Export that commit's `adk/` and `install.sh` into `~/.config/adk/src`, a folder Claude cannot reach, the same verified way. It refuses if the exported files are not exactly the commit's files with the commit's contents, or if the folder is not owned by you or is writable by anyone else.
6. Show the plan and ask `Apply the plan above? [y/N]`. The plan, the user step, the root step and `adk doctor` all run from the exported copy. Before each root step the copy is checked again.
7. Ask for your sudo password. The root part copies files into `/etc` and reloads AppArmor.

## Uninstall and the rescue path

Use this when the wall blocks everything, including sessions that should start normally. The uninstall does not need Claude.

It also needs a commit hash, and it runs the root restore from that commit's export, the same way as the install. Use the hash of the commit you installed, or any reviewed commit. Paste the same text as for the install, with `uninstall "$H"` in place of `"$H"` at the end of the second line (`&& "$D/src/install.sh" uninstall "$H"`).

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
