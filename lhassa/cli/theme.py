"""The Lhasa color palette + Rich theme for the terminal CLI.

Inspired by the city of Lhasa: temple **gold** (the brand), monk-robe deep **red**
(an accent, used sparingly — never a harsh bright red), snow-white **walls** for the
body, and a little sage **green** for "good / cheap / done". A muted stone grey carries
the secondary text. Truecolor hex so it reads the same across terminals; Rich/
prompt-toolkit gracefully degrade on 256-color terminals.
"""

from __future__ import annotations

from rich.theme import Theme

# ── Palette (truecolor) ───────────────────────────────────────────────────────
GOLD = "#d9a521"   # temple gold — brand / primary
RED = "#a23e34"    # monk-robe deep red — accent, sparingly
WHITE = "#ece4d3"  # warm snow-white — body text
GREEN = "#7c9a5b"  # sage — success / good cost
STONE = "#8a8276"  # muted stone — secondary / dim

# Named styles used across the CLI. Keep the body on the terminal default; we color
# the CHROME (prompt, headers, cost, errors) so it stays readable on any terminal.
LHASSA_THEME = Theme(
    {
        "brand": f"bold {GOLD}",
        "accent": RED,
        "prompt": GOLD,
        "caret": f"bold {RED}",
        "success": GREEN,
        "warn": GOLD,
        "error": f"bold {RED}",
        "dim": STONE,
        "muted": STONE,
        "model": GOLD,
        "tokens": STONE,
        "cost": GREEN,
        "rule": STONE,
        "title": f"bold {GOLD}",
        "value": WHITE,
    }
)


def cost_markup(model: str, in_tokens: int, out_tokens: int, cost_usd: float) -> str:
    """The one-line cost footer, as Rich markup (gold model · dim tokens · green cost)."""
    return (
        f"[dim]⤷[/] [model]{model}[/] [dim]·[/] "
        f"[tokens]{in_tokens}+{out_tokens} tok[/] [dim]·[/] "
        f"[cost]${cost_usd:.4f}[/]"
    )


def prompt_fragments():
    """The prompt-toolkit prompt + style: ``lhassa ›`` (gold name, red caret)."""
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.styles import Style

    style = Style.from_dict({"name": f"{GOLD} bold", "caret": f"{RED} bold"})
    text = HTML("<name>lhassa</name> <caret>›</caret> ")
    return text, style
