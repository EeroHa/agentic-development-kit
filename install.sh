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
  export GIT_NO_REPLACE_OBJECTS=1; H=<hash>; R=${ADK_REPO:-$HOME/projects/agentic-dev-kit}; D=$HOME/.config/adk/install-$H
  git -C "$R" fsck --no-dangling --no-progress && rm -rf "$D" && mkdir -p -m 700 "$D" && git -C "$R" cat-file blob "$H:adk/install/export.py" >"$D/export.py" && /usr/bin/python3 -I -B "$D/export.py" export "$R" "$D/src" "$H" && "$D/src/install.sh" "$H"'
set -euo pipefail
# A refs/replace entry makes git serve other bytes under the same hash. Never honour one.
for v in $(compgen -e | grep '^GIT_' || true); do unset "$v"; done  # no GIT_DIR, GIT_OBJECT_DIRECTORY, ...
export GIT_NO_REPLACE_OBJECTS=1
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
[ -d "$REPO" ] && [ "$(git -C "$REPO" rev-parse --show-toplevel 2>/dev/null || true)" = "$(cd "$REPO" && pwd -P)" ] ||
  die "refusing: $REPO is not a git repository (set ADK_REPO, or clone the kit to ~/projects/agentic-dev-kit)"
[ "$(git -C "$REPO" cat-file -t "$hash" 2>/dev/null || true)" = commit ] ||
  die "$hash is not a commit in the repository $REPO"

# 4. Refuse a repository with replace refs, before anything is read from it (export.py checks again).
for gitdir in "$(git -C "$REPO" rev-parse --git-dir)" "$(git -C "$REPO" rev-parse --git-common-dir)"; do
  [[ $gitdir = /* ]] || gitdir="$REPO/$gitdir"
  if [ -n "$(ls -A "$gitdir/refs/replace" 2>/dev/null || true)" ] || grep -qs 'refs/replace/' "$gitdir/packed-refs"; then
    die "refusing: $gitdir has refs/replace entries, which can swap files without changing the hash"
  fi
done
[ -z "$(git -C "$REPO" for-each-ref refs/replace)" ] || die "refusing: git for-each-ref refs/replace lists entries"

# 5. Tripwire: this script and export.py must equal the approved commit's copies.
#    This is a tripwire, not a guarantee. A copy that was edited can simply skip this check, so
#    the only safe way is the documented one: run the copy that export.py wrote under
#    ~/.config/adk (see the usage text). If the check cannot run, refuse.
for f in install.sh adk/install/export.py; do
  cmp -s <(git -C "$REPO" cat-file blob "$hash:$f" 2>/dev/null) "$HERE/$f" ||
    die "refusing: $HERE/$f is not the copy in commit $hash. Run the exported copy: run install.sh without arguments for the commands"
done
$PY -I "$EXPORT" verify "$REPO" "$HERE" "$hash" ||
  die "refusing: $HERE is not exactly the export of commit $hash"

# 6. Show the commit and ask. Nothing from the commit has run yet.
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
