#!/usr/bin/env bash
# Lhassa; per-distro prerequisite bootstrap. Run ONCE with sudo, BEFORE install.sh:
#
#   sudo bash scripts/bootstrap.sh
#
# Detects the distro's package manager (Arch pacman / Debian+Ubuntu apt /
# Fedora dnf / openSUSE zypper) and installs the system prerequisites the
# installer needs: git, curl, python3, nodejs+npm (dashboard build). Everything
# else is user-level and printed as next steps (uv, optional ollama).
# Idempotent: already-installed packages are skipped by the package manager.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run with sudo:  sudo bash scripts/bootstrap.sh" >&2
    exit 1
fi

if command -v pacman >/dev/null 2>&1; then
    echo "→ Arch (pacman)"
    pacman -Sy --needed --noconfirm git curl python nodejs npm
elif command -v apt-get >/dev/null 2>&1; then
    echo "→ Debian/Ubuntu (apt)"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y git curl python3 python3-venv nodejs npm
elif command -v dnf >/dev/null 2>&1; then
    echo "→ Fedora (dnf)"
    dnf install -y git curl python3 nodejs npm
elif command -v zypper >/dev/null 2>&1; then
    echo "→ openSUSE (zypper)"
    zypper --non-interactive install git curl python3 nodejs npm
else
    echo "Unsupported distro: no pacman/apt/dnf/zypper found." >&2
    exit 1
fi

REAL_USER="${SUDO_USER:-}"
echo
echo "System prerequisites installed. Next (as your NORMAL user${REAL_USER:+ ($REAL_USER)}, no sudo):"
echo "  1. curl -LsSf https://astral.sh/uv/install.sh | sh     # uv (Python env manager)"
echo "  2. bash scripts/install.sh                             # Lhassa services"
echo "  3. (optional) install ollama + run: make install-ollama  # real semantic memory"
echo "  4. (optional) sudo bash scripts/install-privileged.sh    # scoped root allowlist"
