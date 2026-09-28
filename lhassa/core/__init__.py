# SPDX-License-Identifier: AGPL-3.0-or-later
"""Core data spine; the append-only SQLite "grande" brain DB and the
structured Episode (observability from the first commit).

Multi-tenant in shape, single-tenant in practice: every row carries an
``instance_id`` and every query filters by it (isolation from day one).
"""
