#!/usr/bin/env bash
# Let bubblewrap (bwrap) create the isolated environments Claude Code's sandbox needs.
#
# Ubuntu 24.04 blocks unprivileged user namespaces for most programs, which makes
# bwrap fail with "setting up uid map: Permission denied". This installs an AppArmor
# profile that allows /usr/bin/bwrap only. Source of the profile text:
# https://code.claude.com/docs/en/sandboxing.md#add-an-apparmor-profile-for-bubblewrap
#
# Usage:
#   scripts/apply-bwrap-apparmor.sh            install the profile, reload AppArmor, test bwrap
#   scripts/apply-bwrap-apparmor.sh --undo    remove the profile, reload AppArmor
#
# Run as your normal user. The script calls sudo itself and asks for your password.
set -euo pipefail

PROFILE=/etc/apparmor.d/bwrap
BACKUP_DIR=/var/backups/agentic-dev-kit

test_bwrap() {
  if bwrap --ro-bind / / --dev /dev --proc /proc true 2>/dev/null; then
    echo "bwrap works: OK"
  else
    echo "bwrap still fails. Check: sysctl kernel.apparmor_restrict_unprivileged_userns" >&2
    echo "and: sudo aa-status | grep bwrap" >&2
    return 1
  fi
}

if [ "${1:-}" = "--undo" ]; then
  if [ -e "$PROFILE" ]; then
    sudo rm -f "$PROFILE"
    sudo systemctl reload apparmor
    echo "Removed $PROFILE and reloaded AppArmor."
  else
    echo "Nothing to undo: $PROFILE does not exist."
  fi
  exit 0
fi

if [ -n "${1:-}" ]; then
  echo "usage: $0 [--undo]" >&2
  exit 2
fi

restriction=$(sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null || echo "missing")
echo "AppArmor userns restriction: $restriction (1 = restricted; the profile is needed)"

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
cat > "$tmp" <<'EOF'
abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap>
}
EOF

if [ -f "$PROFILE" ] && cmp -s "$tmp" "$PROFILE"; then
  echo "Profile already in place: $PROFILE"
else
  if [ -e "$PROFILE" ]; then
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    sudo mkdir -p "$BACKUP_DIR"
    sudo cp -p "$PROFILE" "$BACKUP_DIR/bwrap.$stamp"
    echo "Backed up the previous profile to $BACKUP_DIR/bwrap.$stamp"
  fi
  sudo install -m 0644 -o root -g root "$tmp" "$PROFILE"
  sudo systemctl reload apparmor
  echo "Installed $PROFILE and reloaded AppArmor."
fi

test_bwrap
