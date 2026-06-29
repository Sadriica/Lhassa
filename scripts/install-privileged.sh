#!/usr/bin/env bash
#
# Install the scoped NOPASSWD sudoers drop-in that lets the potts assistant run a
# curated set of admin commands as root. Run with sudo:
#
#   sudo bash scripts/install-privileged.sh
#
# The allowlist below must stay in sync with potts/tools/privileged.py. The file
# is validated with `visudo -c` before it is installed; a bad file is never put
# in place. To remove: sudo rm /etc/sudoers.d/potts
#
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run with sudo:  sudo bash scripts/install-privileged.sh" >&2
    exit 1
fi

# The desktop user that runs potts (the user who invoked sudo).
USER_NAME="${SUDO_USER:-${USER}}"
if [[ -z "${USER_NAME}" || "${USER_NAME}" == "root" ]]; then
    echo "Could not determine the non-root user. Set SUDO_USER or run via sudo." >&2
    exit 1
fi

DST="/etc/sudoers.d/potts"

# Curated allowlist — keep in sync with potts/tools/privileged.py.
ALLOW="/usr/bin/systemctl, /usr/bin/journalctl, /usr/bin/pacman, /usr/bin/tlp, /usr/bin/tlp-stat, /usr/local/bin/power-mode, /usr/bin/dmesg, /usr/bin/sensors"

TMP="$(mktemp)"
trap 'rm -f "${TMP}"' EXIT
{
    echo "# /etc/sudoers.d/potts — scoped passwordless admin for the potts assistant."
    echo "# Managed by potts scripts/install-privileged.sh. Keep in sync with potts/tools/privileged.py."
    echo "${USER_NAME} ALL=(root) NOPASSWD: ${ALLOW}"
} > "${TMP}"

# Validate before installing — a syntax error here must not touch /etc.
if ! visudo -cf "${TMP}"; then
    echo "Generated sudoers file failed validation; nothing was installed." >&2
    exit 1
fi

install -m 0440 -o root -g root "${TMP}" "${DST}"
echo "Installed ${DST} for user '${USER_NAME}'."
echo "potts can now run these as root without a password:"
echo "  ${ALLOW}"
