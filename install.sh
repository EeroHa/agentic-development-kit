#!/usr/bin/env bash
# Install or remove the adk wall. Run it in a terminal, never from inside a Claude session.
#
# The owner types the full hash of a commit he has reviewed. Only that commit is installed
# or uninstalled: nothing from the working tree (which Claude can write to) is ever copied
# to a system location or run, as root or as the owner.
#
# Run the copy of this script that belongs to the approved commit, not the working-tree copy:
USAGE='usage:
  install.sh <40-hex-commit>              install that commit
  install.sh uninstall <40-hex-commit>    uninstall, using that commit

Run the copy of install.sh from the approved commit, not the one in the working tree:
  export TMPDIR=${TMPDIR:-/tmp}
  rm -rf "$TMPDIR/adk-install" && mkdir -m 700 "$TMPDIR/adk-install"
  git -C ~/projects/agentic-dev-kit archive <hash> install.sh adk | tar -x -C "$TMPDIR/adk-install" && "$TMPDIR/adk-install/install.sh" <hash>
and for uninstall:  ... && "$TMPDIR/adk-install/install.sh" uninstall <hash>'
set -euo pipefail
# A refs/replace entry makes git serve other bytes under the same hash. Never honour one.
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

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
SRC="$HOME/.config/adk/src"
STAGING="$HOME/.config/adk/staging"
BACKUPS=/var/backups/agentic-dev-kit
PY=/usr/bin/python3
EXPORT="$HERE/adk/install/export.py"

# 2. The repository: the git repo of the current folder, ADK_REPO, or ~/projects/agentic-dev-kit,
#    whichever holds the typed commit first.
REPO=
for cand in "$(git rev-parse --show-toplevel 2>/dev/null || true)" "${ADK_REPO:-}" "$HOME/projects/agentic-dev-kit"; do
  if [ -n "$cand" ] && [ "$(git -C "$cand" cat-file -t "$hash" 2>/dev/null || true)" = commit ]; then
    REPO=$cand
    break
  fi
done
[ -n "$REPO" ] || die "$hash is not a commit in the current folder's repository, in \$ADK_REPO or in ~/projects/agentic-dev-kit"

# 3. Refuse a repository with replace refs, before anything is read from it (export.py checks again).
for gitdir in "$(git -C "$REPO" rev-parse --git-dir)" "$(git -C "$REPO" rev-parse --git-common-dir)"; do
  [[ $gitdir = /* ]] || gitdir="$REPO/$gitdir"
  if [ -n "$(ls -A "$gitdir/refs/replace" 2>/dev/null || true)" ] || grep -qs 'refs/replace/' "$gitdir/packed-refs"; then
    die "refusing: $gitdir has refs/replace entries, which can swap files without changing the hash"
  fi
done
[ -z "$(git -C "$REPO" for-each-ref refs/replace)" ] || die "refusing: git for-each-ref refs/replace lists entries"

# 4. Tripwire: this script and export.py must equal the approved commit's copies.
#    This is a tripwire, not a guarantee. A copy that was edited can simply skip this check, so
#    the only safe way is the documented one: extract install.sh and adk/ from the commit
#    with git archive (see the usage text) and run that extracted copy. If the check cannot run,
#    refuse.
for f in install.sh adk/install/export.py; do
  cmp -s <(git -C "$REPO" cat-file blob "$hash:$f" 2>/dev/null) "$HERE/$f" ||
    die "refusing: $HERE/$f is not the copy in commit $hash. Run the extracted copy: run install.sh without arguments for the commands"
done

# 5. Show the commit and ask. Nothing from the commit has run yet.
subject=$($PY -I "$EXPORT" info "$REPO" "$hash")
verb=Install
[ "$action" = install ] || verb=Uninstall
echo "Commit:  $hash"
echo "Subject: $subject"
ask "$verb commit $hash ($subject)? [y/N]" || { echo "Nothing changed."; exit 0; }

# 6. Export exactly that commit. Every step below runs from the exported copy.
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
