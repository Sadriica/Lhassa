# SPDX-License-Identifier: AGPL-3.0-or-later
"""The machine side of the "check-in, not push" loop (§2.5).

A thin local agent runs on each machine and talks *outward* to the brain over
HTTP (TLS/WireGuard tunnel). It never accepts a push: it checks in, receives
signed Tasks, runs each ONLY after its own allowlist gate (§2.6) accepts it, and
reports the outcome back. See ``lhassa.remote.local_agent``.
"""
