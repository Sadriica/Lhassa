# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shell-level read-models: composed, read-only views over the whole brain.

A view aggregates the spine, the interfaces and the adapters, and may read other
shells (the org chart, the flow store) to do it, so it sits at the shell level of
the layering: surfaces (the CLI, the api pack, the TUI) render it, and no inner
layer may import it. Views never write and never call a model.
"""
