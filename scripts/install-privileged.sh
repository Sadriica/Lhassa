#!/usr/bin/env bash
#
# Install the scoped NOPASSWD sudoers drop-in that lets the lhassa assistant run a
# curated set of admin commands as root. Run with sudo:
#
#   sudo bash scripts/install-privileged.sh
#
# The allowlist below must stay in sync with lhassa/tools/privileged.py. The file
# is validated with `visudo -c` before it is installed; a bad file is never put
# in place. To remove: sudo rm /etc/sudoers.d/lhassa
#
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run with sudo:  sudo bash scripts/install-privileged.sh" >&2
    exit 1
fi

# The desktop user that runs lhassa (the user who invoked sudo).
USER_NAME="${SUDO_USER:-${USER}}"
if [[ -z "${USER_NAME}" || "${USER_NAME}" == "root" ]]; then
    echo "Could not determine the non-root user. Set SUDO_USER or run via sudo." >&2
    exit 1
fi

DST="/etc/sudoers.d/lhassa"

# Curated allowlist — built PER HOST: universal admin tools + THIS distro's
# package manager + optional laptop tools, each included only when the binary
# actually exists (sudoers requires absolute paths; a missing binary is skipped,
# never guessed). Keep the candidate set in sync with lhassa/tools/privileged.py.
CANDIDATES=(
    # Universal admin/observability.
    systemctl journalctl dmesg sensors
    # One of these is this distro's package manager (Arch/Debian+Ubuntu/Fedora/openSUSE).
    pacman apt-get apt dnf zypper
    # Laptop/power tools — present on the reference laptop, absent on servers.
    tlp tlp-stat power-mode
)
ALLOW=""
for cmd in "${CANDIDATES[@]}"; do
    path="$(command -v "${cmd}" 2>/dev/null || true)"
    # Only absolute system paths — refuse anything odd a user PATH could inject.
    case "${path}" in
        /usr/bin/*|/usr/sbin/*|/bin/*|/sbin/*|/usr/local/bin/*|/usr/local/sbin/*)
            ALLOW="${ALLOW:+${ALLOW}, }${path}"
            ;;
    esac
done
if [[ -z "${ALLOW}" ]]; then
    echo "No allowlisted binaries found on this host; nothing to install." >&2
    exit 1
fi

TMP="$(mktemp)"
trap 'rm -f "${TMP}"' EXIT
{
    echo "# /etc/sudoers.d/lhassa — scoped passwordless admin for the lhassa assistant."
    echo "# Managed by lhassa scripts/install-privileged.sh. Keep in sync with lhassa/tools/privileged.py."
    echo "${USER_NAME} ALL=(root) NOPASSWD: ${ALLOW}"
} > "${TMP}"

# Validate before installing — a syntax error here must not touch /etc.
if ! visudo -cf "${TMP}"; then
    echo "Generated sudoers file failed validation; nothing was installed." >&2
    exit 1
fi

install -m 0440 -o root -g root "${TMP}" "${DST}"
echo "Installed ${DST} for user '${USER_NAME}'."
echo "lhassa can now run these as root without a password:"
echo "  ${ALLOW}"
