#!/usr/bin/env bash
# Install or remove the adk wall. Run it in a terminal, never from inside a Claude session.
#
# The owner types the full hash of a commit he has reviewed. Only that commit is installed
# or uninstalled: nothing from the working tree (which Claude can write to) is ever copied
# to a system location or run, as root or as the owner.
#
# Run the copy of this script that the verified export of the approved commit made, never the
# working-tree copy. install.sh refuses to run from anywhere else.
USAGE='usage:
  install.sh <40-hex-commit>              install that commit
  install.sh uninstall <40-hex-commit>    uninstall, using that commit

Do not run this file from the repository. Run the verified export of the approved commit
(replace <hash> with the full hash you reviewed; for uninstall add the word uninstall before "$H"):
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
PY'
set -euo pipefail
# A refs/replace entry makes git serve other bytes under the same hash. Never honour one.
for v in $(compgen -e | grep '^GIT_' || true); do unset "$v"; done  # no GIT_DIR, GIT_OBJECT_DIRECTORY, ...
export GIT_NO_REPLACE_OBJECTS=1
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_NO_LAZY_FETCH=1  # no system or global git config
export PYTHONDONTWRITEBYTECODE=1

usage() { echo "$USAGE" >&2; exit 2; }
# Ask a question; anything but a plain "y" (including no input at all) is a no.
ask() { printf '%s ' "$1"; read -r answer || answer=n; [ "$answer" = y ]; }
die() { echo "install.sh: $*" >&2; exit 1; }

# 1. Arguments. Nothing else happens before they are valid.
case "$#" in
  1) action=install; hash=$1 ;;
  2) [ "$1" = uninstall ] || usage; action=uninstall; hash=$2 ;;
  *) usage ;;
esac
[[ $hash =~ ^[0-9a-f]{40}$ ]] || usage

# 2. Run only from the verified export under ~/.config/adk, which Claude cannot write to.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
WANT="$HOME/.config/adk/install-$hash/src"
[ -d "$WANT" ] && [ ! -L "$HOME/.config/adk" ] && [ ! -L "$HOME/.config/adk/install-$hash" ] && [ ! -L "$WANT" ] &&
  [ "$HERE" = "$(cd "$WANT" && pwd -P)" ] ||
  die "refusing: run install.sh from $WANT, made by the export (run install.sh without arguments for the commands), not from $HERE"
SRC="$HOME/.config/adk/src"
STAGING="$HOME/.config/adk/staging"
BACKUPS=/var/backups/agentic-dev-kit
PY=/usr/bin/python3
EXPORT="$HERE/adk/install/export.py"

# 3. One fixed repository, the one the documented one-liner reads from: $ADK_REPO, else
#    ~/projects/agentic-dev-kit. Never the repository of the current folder.
REPO=${ADK_REPO:-$HOME/projects/agentic-dev-kit}
GITDIR="$REPO/.git"
[ -d "$GITDIR" ] && [ ! -L "$GITDIR" ] ||
  die "refusing: $REPO is not a git repository (.git must be a plain folder; set ADK_REPO, or clone the kit to ~/projects/agentic-dev-kit)"

# 4. Refuse a repository that makes git read config, hooks, objects or history from elsewhere. These are
#    plain file checks and they come before the first git command: git runs the config it finds.
#    (export.py checks again.)
for f in commondir info/grafts shallow; do
  [ -z "$(ls -d "$GITDIR/$f" 2>/dev/null)" ] || die "refusing: $GITDIR/$f exists (it makes git read config, objects or history from elsewhere)"
done
ALT="$GITDIR/objects/info/alternates"
if [ -e "$ALT" ] || [ -L "$ALT" ]; then
  { [ -f "$ALT" ] && [ ! -L "$ALT" ] && [ ! -s "$ALT" ]; } || die "refusing: $ALT is not an empty file (it makes git read objects from elsewhere)"
fi
REPLACE="$GITDIR/refs/replace"
if [ -e "$REPLACE" ] || [ -L "$REPLACE" ]; then
  { [ -d "$REPLACE" ] && [ ! -L "$REPLACE" ] && [ -z "$(ls -A "$REPLACE")" ]; } ||
    die "refusing: $REPLACE has entries (refs/replace can swap files without changing the hash)"
fi
! grep -qs 'refs/replace/' "$GITDIR/packed-refs" || die "refusing: $GITDIR/packed-refs has refs/replace entries"

# Every git call: the pinned git dir, no pager, no fsmonitor, no hooks (and no system or global config, set above).
gitq() { git --git-dir="$GITDIR" --no-pager -c core.pager=cat -c core.fsmonitor=false -c core.hooksPath=/dev/null "$@"; }
[ -z "$(gitq for-each-ref refs/replace)" ] || die "refusing: git for-each-ref refs/replace lists entries"
[ "$(gitq cat-file -t "$hash" 2>/dev/null || true)" = commit ] ||
  die "$hash is not a commit in the repository $REPO"

# 5. Tripwire: this script and export.py must equal the approved commit's copies.
#    This is a tripwire, not a guarantee. A copy that was edited can simply skip this check, so
#    the only safe way is the documented one: run the copy that export.py wrote under
#    ~/.config/adk (see the usage text). If the check cannot run, refuse.
for f in install.sh adk/install/export.py; do
  cmp -s <(gitq cat-file blob "$hash:$f" 2>/dev/null) "$HERE/$f" ||
    die "refusing: $HERE/$f is not the copy in commit $hash. Run the exported copy: run install.sh without arguments for the commands"
done
$PY -I "$EXPORT" verify "$REPO" "$HERE" "$hash" ||
  die "refusing: $HERE is not exactly the export of commit $hash"

# 6. Show the commit and ask. The hash you typed is the approval: the commit's bootstrap, export.py and this
#    script already ran to get here. The prompt below only catches a typo.
subject=$($PY -I "$EXPORT" info "$REPO" "$hash")
verb=Install
[ "$action" = install ] || verb=Uninstall
echo "Commit:  $hash"
echo "Subject: $subject"
ask "$verb commit $hash ($subject)? [y/N]" || { echo "Nothing changed."; exit 0; }

# 7. Export exactly that commit. Every step below runs from the exported copy.
$PY -I "$EXPORT" export "$REPO" "$SRC" "$hash"
adk() { (cd "$SRC" && $PY -B -E -s -m adk "$@"); }
# Right before each root step: the exported files are still the commit's, owned by the owner, not writable by others.
verify() { $PY -I "$EXPORT" verify "$REPO" "$SRC" "$hash"; }
APPLY_ROOT="$SRC/adk/install/apply_root.py"

case "$action" in
  install)
    adk install plan
    ask "Apply the plan above? [y/N]" || { echo "Nothing changed."; exit 0; }
    verify
    adk install apply-user
    verify
    changed=$(sudo $PY -I "$APPLY_ROOT" apply "$STAGING" "$BACKUPS")
    echo "$changed"
    if grep -q 'etc/apparmor.d/bwrap' <<<"$changed"; then
      sudo systemctl reload apparmor
    fi
    adk doctor
    ;;
  uninstall)
    verify
    sudo $PY -I "$APPLY_ROOT" restore "$BACKUPS"
    adk install restore-user
    sudo systemctl reload apparmor
    ;;
esac
