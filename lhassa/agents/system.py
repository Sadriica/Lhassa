"""SystemAgent — system administration tasks, OS-aware."""

import platform

from lhassa.agents.base import BaseAgent

_SYSTEM = platform.system()

if _SYSTEM == "Darwin":
    _OS_CONTEXT = (
        "You are running on macOS. "
        "Use Homebrew (brew) for package management and launchctl for services. "
        "Be aware of macOS-specific paths (/usr/local, /opt/homebrew) and conventions."
    )
elif _SYSTEM == "Windows":
    _OS_CONTEXT = (
        "You are running on Windows. "
        "Use winget or chocolatey for package management, "
        "sc/net for services, and PowerShell for system tasks. "
        "Prefer PowerShell over cmd.exe."
    )
else:
    _OS_CONTEXT = (
        "You are running on Linux. "
        "Detect the distro when relevant (Arch: pacman/yay; Debian/Ubuntu: apt; Fedora: dnf). "
        "Use systemctl for service management."
    )


class SystemAgent(BaseAgent):
    name = "system"
    system_prompt = f"""You are a system administration expert.
{_OS_CONTEXT}
When given system tasks:
- Provide exact shell commands for the current OS
- Use the native package manager and service manager
- Be aware of OS-specific paths and conventions
Format shell commands as plain text, one command per line.
Always explain what each command does in one sentence."""
