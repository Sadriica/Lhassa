# SPDX-License-Identifier: AGPL-3.0-or-later
# ``lhassa`` is shared by the core distribution and every capability pack
# (packages/lhassa-<x>/src/lhassa/<x>): a pkgutil-style namespace, so each
# pack's subpackage joins ``lhassa.*`` without editing core.
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
