# SPDX-License-Identifier: AGPL-3.0-or-later
"""RBAC; a small, explicit role→permission matrix for the organizational layer.

Pure and table-driven: no I/O, no DB, no LLM. It answers one question; "may this
ROLE perform this ACTION on this RESOURCE?": from a closed permission table, so
the policy is auditable at a glance (the brief's "qué rol puede hacer qué sobre
qué datos", §3-Fase 7).

The two roles of the minimal two-brain case:
  - ``general``   : the coordinator/curator. Routes (assigns) typed tasks,
    collects results, and is the SOLE writer of the commons. It coordinates; it
    does not execute department work, and that asymmetry lives in the matrix.
  - ``department``: an executor. Reads the commons, owns (reads+writes) its OWN
    private memory, and handles tasks the general assigns. It may NOT write the
    commons, may NOT assign tasks, and may NOT touch ANOTHER department's data.

``actor_id`` (already seeded across the codebase) names WHO acts; this module
governs WHAT a role may do. Multi-person approval (separating who proposes from
who approves) is enforced in ``coordinator`` on top of these permissions.
"""

# Resources the matrix governs (kept as plain strings; table-driven, no I/O):
#   "commons"         : the read-only cross-cutting source of truth
#   "task"            : a unit of routed work
#   "own_memory"      : a brain's OWN private memory/data
#   "department"      : any/another department's private data (cross-cutting read)
#   "message"         : the typed inter-brain queue
#   "status"          : status reporting
#
# Each role maps to the closed set of (action, resource) pairs it is allowed.
_MATRIX: dict[str, set[tuple[str, str]]] = {
    "general": {
        ("read", "commons"),
        ("write", "commons"),      # SOLE curator of the commons
        ("assign", "task"),        # routes typed work to a department
        ("collect", "task"),       # gathers the results
        ("send", "message"),
        ("read", "own_memory"),
        ("write", "own_memory"),
        ("read", "department"),    # may read ANY department's data (oversight)
    },
    "department": {
        ("read", "commons"),       # reads the shared truth …
        ("read", "own_memory"),
        ("write", "own_memory"),   # … and owns only ITS OWN memory
        ("handle", "task"),        # executes the work the general assigned
        ("send", "message"),
        ("report", "status"),
    },
}


def can(role: str, action: str, resource: str) -> bool:
    """Return ``True`` iff ``role`` is permitted ``action`` on ``resource``.

    Pure table lookup against ``_MATRIX``. An unknown role (or any pair not
    explicitly granted) is denied; deny-by-default.
    """
    return (action, resource) in _MATRIX.get(role, set())


def require(role: str, action: str, resource: str) -> None:
    """Assert a permission; raise :class:`PermissionError` on a violation.

    The error message names the role/action/resource so a denial is legible in a
    log or test failure.
    """
    if not can(role, action, resource):
        raise PermissionError(
            f"RBAC denied: role={role!r} may not {action!r} on {resource!r}"
        )
