#!/usr/bin/env bash
# Install or remove the adk wall. Run in a terminal; it works from any folder.
#   ./install.sh            show the plan, ask, then install
#   ./install.sh uninstall  undo every install step
#
# Everything runs from a copy of the committed HEAD exported to ~/.config/adk/src,
# a folder Claude cannot reach, never from the working tree Claude can write to.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

SRC="$HOME/.config/adk/src"
STAGING="$HOME/.config/adk/staging"
BACKUPS=/var/backups/agentic-dev-kit
APPLY_ROOT="$SRC/adk/install/apply_root.py"
PY=/usr/bin/python3
export PYTHONDONTWRITEBYTECODE=1

# Run the committed copy of export.py: refuses a dirty tree, exports HEAD, prints "<hash> <branch>".
export_src() {
  git show HEAD:adk/install/export.py | $PY -I - "$PWD" "$SRC"
}

adk() {
  (cd "$SRC" && $PY -m adk "$@")
}

case "${1:-install}" in
  install)
    exported=$(export_src)
    read -r commit branch <<<"$exported"
    echo "Installing commit $commit on branch $branch, exported to $SRC"
    adk install plan
    read -r -p "Install commit $commit ($branch)? [y/N] " answer
    [ "$answer" = "y" ] || { echo "Nothing changed."; exit 0; }
    adk install apply-user
    changed=$(sudo /usr/bin/python3 -I "$APPLY_ROOT" apply "$STAGING" "$BACKUPS")
    echo "$changed"
    if grep -q 'etc/apparmor.d/bwrap' <<<"$changed"; then
      sudo systemctl reload apparmor
    fi
    adk doctor
    ;;
  uninstall)
    export_src >/dev/null
    sudo /usr/bin/python3 -I "$APPLY_ROOT" restore "$BACKUPS"
    adk install restore-user
    sudo systemctl reload apparmor
    ;;
  *)
    echo "usage: ./install.sh [uninstall]" >&2
    exit 2
    ;;
esac
