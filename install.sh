#!/usr/bin/env bash
# Install or remove the adk wall. Run from the repo root, in a terminal.
#   ./install.sh            show the plan, ask, then install
#   ./install.sh uninstall  undo every install step
set -euo pipefail

STAGING="$HOME/.config/adk/staging"
BACKUPS=/var/backups/agentic-dev-kit
APPLY_ROOT=adk/install/apply_root.py

case "${1:-install}" in
  install)
    python3 -m adk install plan
    read -r -p "Apply? [y/N] " answer
    [ "$answer" = "y" ] || { echo "Nothing changed."; exit 0; }
    python3 -m adk install apply-user
    changed=$(sudo /usr/bin/python3 -I "$APPLY_ROOT" apply "$STAGING" "$BACKUPS")
    echo "$changed"
    if grep -q 'etc/apparmor.d/bwrap' <<<"$changed"; then
      sudo systemctl reload apparmor
    fi
    if python3 -m adk doctor --help >/dev/null 2>&1; then
      python3 -m adk doctor
    fi
    ;;
  uninstall)
    sudo /usr/bin/python3 -I "$APPLY_ROOT" restore "$BACKUPS"
    python3 -m adk install restore-user
    sudo systemctl reload apparmor
    ;;
  *)
    echo "usage: ./install.sh [uninstall]" >&2
    exit 2
    ;;
esac
