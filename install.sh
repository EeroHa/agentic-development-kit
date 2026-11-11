#!/usr/bin/env bash
# Install or remove the adk wall. Run from the repo root, in a terminal.
#   ./install.sh            show the plan, ask, then install
#   ./install.sh uninstall  undo every install step
set -euo pipefail

STAGING="$HOME/.config/adk/staging"
BACKUPS=/var/backups/agentic-dev-kit
APPLY_ROOT=adk/install/apply_root.py
PY=/usr/bin/python3

case "${1:-install}" in
  install)
    # These files get copied to root-owned locations and one of them runs as root,
    # so only install a state that has been committed and reviewed.
    if [ -n "$(git status --porcelain -- adk install.sh)" ]; then
      echo "Uncommitted changes under adk/ or in install.sh. Review and commit them first." >&2
      exit 1
    fi
    echo "Installing from commit $(git rev-parse --short HEAD) on branch $(git rev-parse --abbrev-ref HEAD)"
    $PY -m adk install plan
    read -r -p "Apply? [y/N] " answer
    [ "$answer" = "y" ] || { echo "Nothing changed."; exit 0; }
    $PY -m adk install apply-user
    changed=$(sudo /usr/bin/python3 -I "$APPLY_ROOT" apply "$STAGING" "$BACKUPS")
    echo "$changed"
    if grep -q 'etc/apparmor.d/bwrap' <<<"$changed"; then
      sudo systemctl reload apparmor
    fi
    if $PY -m adk doctor --help >/dev/null 2>&1; then
      $PY -m adk doctor
    fi
    ;;
  uninstall)
    sudo /usr/bin/python3 -I "$APPLY_ROOT" restore "$BACKUPS"
    $PY -m adk install restore-user
    sudo systemctl reload apparmor
    ;;
  *)
    echo "usage: ./install.sh [uninstall]" >&2
    exit 2
    ;;
esac
