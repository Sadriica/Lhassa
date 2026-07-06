"""Seed the personal secretary department and its morning/evening flows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from lhassa.personal.secretary import ensure_personal_secretary
from lhassa.seeds import register_seed

if TYPE_CHECKING:
    from lhassa.config import Config


@register_seed(
    "personal",
    "Create the personal secretary department plus the morning and evening briefing flows.",
)
def apply(config: "Optional[Config]" = None) -> dict:
    return ensure_personal_secretary(config)
