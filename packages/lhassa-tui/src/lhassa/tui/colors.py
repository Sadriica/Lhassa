# SPDX-License-Identifier: AGPL-3.0-or-later
"""The TUI's color source: one palette, derived from ``cli.theme``'s canonical
GOLD/RED/WHITE/GREEN/STONE, fed into Textual as CSS variables.

No new brand colors are introduced. The dark ramp mirrors the dashboard's
"Matte Obsidian" (``dashboard/src/index.css``): a near-black surface with gold
reserved for *text*, never a fill. The light ramp keeps the same hues and
inverts luminance; it is derived from the same constants (a numeric blend
toward the dark palette's own ``bg``, or the dashboard's documented
``--lh-gold-deep``), not invented.

:func:`detect_theme` picks dark/light in this order: ``LHASSA_TUI_THEME``
(``dark``/``light``/``auto``) → ``COLORFGBG`` → a short-timeout OSC 11
terminal probe → dark, on ANY ambiguity or error. Nothing here may raise;
a palette-detection failure degrades silently to dark.
"""

from __future__ import annotations

import os
import re

from lhassa.cli.theme import GOLD, GREEN, RED, STONE, WHITE

# ── canonical dark ramp (mirrors dashboard/src/index.css) ────────────────────
_BG = "#14110d"
_SURFACE = "#1c1813"
_SURFACE_2 = "#211c16"
_BORDER = "rgba(236, 228, 211, 0.07)"
_FAINT = "#6b6458"
_GOLD_SOFT = "#e8bf55"
_ACCENT_ON = "#14110d"
_GOLD_DEEP = "#b9881a"  # dashboard's --lh-gold-deep


def _mix(hex_a: str, hex_b: str, t: float) -> str:
    """Linear-blend two ``#rrggbb`` colors; ``t=0`` -> a, ``t=1`` -> b."""
    a = tuple(int(hex_a[i : i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(hex_b[i : i + 2], 16) for i in (1, 3, 5))
    mixed = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return "#" + "".join(f"{c:02x}" for c in mixed)


DARK: dict[str, str] = {
    "lh-bg": _BG,
    "lh-surface": _SURFACE,
    "lh-surface-2": _SURFACE_2,
    "lh-border": _BORDER,
    "lh-faint": _FAINT,
    "lh-text": WHITE,
    "lh-gold": GOLD,
    "lh-gold-soft": _GOLD_SOFT,
    "lh-accent-on": _ACCENT_ON,
    "lh-red": RED,
    "lh-green": GREEN,
    "lh-stone": STONE,
}

# Light variant: same hues, inverted luminance. Surfaces are the warm white
# already used as body text (``WHITE``), tinted a hair darker for the two
# tiers; gold darkens to the dashboard's own ``--lh-gold-deep`` so it stays
# legible on a light background (per the plan, not a new color).
LIGHT: dict[str, str] = {
    "lh-bg": WHITE,
    "lh-surface": _mix(WHITE, _BG, 0.04),
    "lh-surface-2": _mix(WHITE, _BG, 0.08),
    "lh-border": "rgba(20, 17, 13, 0.12)",
    "lh-faint": STONE,
    "lh-text": _BG,
    "lh-gold": _GOLD_DEEP,
    "lh-gold-soft": _GOLD_DEEP,
    "lh-accent-on": _BG,
    "lh-red": RED,
    "lh-green": GREEN,
    "lh-stone": STONE,
}


def _configured_theme() -> str:
    """The ``LHASSA_TUI_THEME`` knob, lower-cased. Never raises."""
    try:
        from lhassa.config import Config

        value = getattr(Config, "tui_theme", "") or ""
    except Exception:
        value = os.getenv("LHASSA_TUI_THEME", "")
    return str(value).strip().lower()


def _from_colorfgbg() -> "str | None":
    """Best-effort read of ``COLORFGBG`` (``fg;bg``, xterm palette indices)."""
    raw = os.getenv("COLORFGBG", "")
    if not raw:
        return None
    try:
        bg = int(raw.split(";")[-1])
    except (ValueError, IndexError):
        return None
    # Palette indices 7/15 are the light greys/white; everything else in the
    # 16-color range reads as a dark background.
    return "light" if bg in (7, 15) else "dark"


def _probe_osc11(timeout: float = 0.15) -> "str | None":
    """Query the terminal's background color via OSC 11. ``None`` on ANY
    ambiguity; not a tty, no termios, no reply in time, a garbled reply."""
    try:
        import sys

        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return None
        import select
        import termios
        import tty
    except Exception:
        return None

    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        return None

    response = ""
    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b]11;?\x1b\\")
        sys.stdout.flush()
        ready, _, _ = select.select([fd], [], [], timeout)
        if ready:
            response = os.read(fd, 256).decode("ascii", errors="ignore")
    except Exception:
        return None
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass

    match = re.search(r"rgb:([0-9a-fA-F]{2,4})/([0-9a-fA-F]{2,4})/([0-9a-fA-F]{2,4})", response)
    if not match:
        return None
    try:
        r, g, b = (int(part[:2], 16) for part in match.groups())
    except ValueError:
        return None
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "light" if luminance >= 0.5 else "dark"


def detect_theme() -> str:
    """Resolve ``dark``/``light``. Never raises; defaults to ``dark``."""
    try:
        configured = _configured_theme()
        if configured in ("dark", "light"):
            return configured
        probed = _from_colorfgbg() or _probe_osc11()
        return probed if probed in ("dark", "light") else "dark"
    except Exception:
        return "dark"


def variables(theme: "str | None" = None) -> dict[str, str]:
    """The ``$lh-*`` CSS variables for ``theme`` (or the auto-detected one)."""
    resolved = theme if theme in ("dark", "light") else detect_theme()
    return dict(LIGHT if resolved == "light" else DARK)
