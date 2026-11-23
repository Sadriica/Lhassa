# SPDX-License-Identifier: AGPL-3.0-or-later
"""The cv pack's own ``Config`` knobs (imported by the package itself).

Declared through :func:`lhassa.config.register_config_knob` so core's class
body knows nothing about CVs.
"""

from __future__ import annotations

from lhassa.config import register_config_knob

# The stored work-flow id the Telegram CV bridge runs when the owner sends a CV
# document (the "Impossible-to-reject CV" flow). A missing flow degrades to a
# "not set up yet" reply, never a crash.
register_config_knob("cv_flow_id", "LHASSA_CV_FLOW_ID", "flow-zvzpry")
# LaTeX engine that compiles a tailored CV .tex into a PDF; resolved via
# shutil.which, so a machine without it degrades to sending the raw .tex.
register_config_knob("tectonic_bin", "LHASSA_TECTONIC_BIN", "tectonic")
