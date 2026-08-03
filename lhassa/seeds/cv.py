# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ``cv`` seed — recreate the "Impossible-to-reject CV" pipeline from code.

This is the FIRST registered seed (see :mod:`lhassa.seeds`); future seeds drop in the
same way. Applying it reproduces, on a fresh machine, everything we hand-built for the
CV pipeline EXCEPT the owner's real base CV (which stays user-provided):

  * the ``interns`` department under ``general`` (created if missing),
  * its 6 pinned worker brains — ``jd-analyst``, ``researcher``, ``cv-writer``,
    ``ats-evaluator``, ``critic``, ``finalizer`` — each a leaf under ``interns``,
  * the ``interns`` brain wired as the environment MONITOR/supervisor
    (:func:`lhassa.org.monitor.make_brain_monitor`),
  * the canonical ``flow-zvzpry`` work-flow (trigger → analyze → research → draft →
    ATS score → verify gate → critique → finalize → contact-preserved gate → tex
    output), captured VERBATIM below from the live definition as the source of truth,
  * a PLACEHOLDER ``cv_largo.tex`` — written ONLY when none exists, so a real base CV
    is never overwritten.

Guarantees (matching the rest of Lhassa): idempotent (existing brains are skipped, no
duplicates), non-destructive (an existing base CV is kept; overwriting the flow
DEFINITION is fine — it is code-owned config and drops no brains), and it never raises
destructively — a partial failure is reported in the returned summary, not fatal.

The org scaffold + monitor wiring go through the validated org management API
(:mod:`lhassa.org.management`), the flow through :class:`lhassa.flows.store.FlowStore`,
and the base CV through the :mod:`lhassa.cv` file-I/O seam.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

from loguru import logger

from lhassa.cv import paths as cv_paths
from lhassa.flows.model import Flow
from lhassa.flows.store import FlowStore
from lhassa.org import management
from lhassa.org.monitor import make_brain_monitor
from lhassa.org.topology import load_topology
from lhassa.seeds import register_seed

if TYPE_CHECKING:
    from lhassa.config import Config

#: The canonical CV work-flow id (overridable via ``config.cv_flow_id`` or the
#: ``flow_id`` argument). Kept as the live id so a fresh env matches what we built.
DEFAULT_CV_FLOW_ID = "flow-zvzpry"

#: The coordinator the interns department hangs under.
GENERAL_ID = "general"

#: The interns department brain id + its department/identity label.
INTERNS_ID = "interns"
INTERNS_DEPARTMENT = "interns"

#: The 6 pinned worker brains, each a leaf executing under ``interns``. Their ids
#: match the ``brain`` pins in the canonical flow's task nodes.
CV_WORKER_BRAINS: tuple[str, ...] = (
    "jd-analyst",
    "researcher",
    "cv-writer",
    "ats-evaluator",
    "critic",
    "finalizer",
)

# A minimal, compilable placeholder base CV. Written ONLY when no ``cv_largo.tex``
# exists yet, so the owner's real base CV is never overwritten. The contact details
# are obvious placeholders the owner replaces with their real long-form CV.
_PLACEHOLDER_BASE_CV = r"""\documentclass[11pt,a4paper]{article}
\usepackage[margin=2cm]{geometry}
\usepackage{hyperref}
\begin{document}

\begin{center}
  {\Large \textbf{Your Name}}\\
  your.email@example.com $\cdot$ +00 000 000 000 $\cdot$ City, Country\\
  \href{https://example.com}{example.com}
\end{center}

\section*{Summary}
Replace this placeholder with your real long-form CV (\texttt{cv\_largo.tex}). The CV
pipeline tailors THIS document per job posting; it never invents experience, so keep
everything here truthful and complete.

\section*{Experience}
\textbf{Role} --- Company \hfill Dates\\
Achievements and responsibilities.

\section*{Education}
\textbf{Degree} --- Institution \hfill Year

\section*{Skills}
List your real skills, tools and technologies here.

\end{document}
"""

# ── the canonical CV work-flow, captured VERBATIM from the live ``flow-zvzpry`` ────
# This is the SOURCE OF TRUTH: the exact JSON of the flow we built (all task prompts —
# incl. the <=2-page / never-invent / contact-verbatim / LaTeX-only rules — and their
# max_tokens budgets, the two gates: gate-1 verify + gate-2 {type: contact_preserved},
# and the output {type: tex}). Embedded as a raw string and parsed at build time so it
# round-trips exactly. The ``id`` is overridden with the resolved flow id when built.
_CV_FLOW_JSON = r'''{
    "id": "flow-zvzpry",
    "name": "Impossible-to-reject CV",
    "description": "From a job posting + candidate context, research → draft → score → critique → synthesize a CV that is hard to reject.",
    "nodes": [
        {
            "id": "trigger-1",
            "kind": "trigger",
            "config": {
                "type": "manual"
            },
            "label": "Context: job post + candidate"
        },
        {
            "id": "task-1",
            "kind": "task",
            "config": {
                "brain": "jd-analyst",
                "prompt": "From {input}, extract the role's hard requirements, must-have keywords, and ATS terms. Also identify the hiring company / convocatoria name and emit it as `company` in the context. Return a structured brief.",
                "max_tokens": 1200
            },
            "label": "Analyze job posting"
        },
        {
            "id": "task-2",
            "kind": "task",
            "config": {
                "brain": "researcher",
                "prompt": "Deep-search the company, role, industry and what makes a candidate stand out for {input}. Return signals to weave into the CV.",
                "max_tokens": 1500
            },
            "label": "Deep research"
        },
        {
            "id": "task-3",
            "kind": "task",
            "config": {
                "brain": "cv-writer",
                "prompt": "Build on the candidate's base CV below, tailoring it for {company}. Do NOT invent experience: tighten, strengthen and reorder what is already there so it maps to the brief's must-have keywords and ATS terms. PRESERVE the base CV's structure, sections and LaTeX formatting; keep it to a MAX of 2 pages per role. Output valid LaTeX.\n\n--- BASE CV (cv_largo.tex) ---\n{base_cv}\n--- END BASE CV ---\n\nUsing the brief and research, produce 3 tailored LaTeX CV alternatives, each optimizing the base above. Output ONLY the complete LaTeX document, starting with \\documentclass and ending with \\end{document} — no prose, no explanations, no markdown fences. HARD RULES: copy ALL contact details (name, email, phone, links, location) EXACTLY and verbatim from the base CV — never alter, reformat, or invent them. Do NOT invent or add tools, technologies, employers, dates, metrics or experience that are not already in the base CV — only rephrase, reorder and emphasize what genuinely exists there. Length: target ~1.5 pages and NEVER exceed 2 pages (2 is a hard maximum, aim a little under) -- if it does not fit, tighten wording and cut the LEAST relevant bullets and the oldest/least-relevant roles FIRST (never touch contact details or the most relevant experience).",
                "max_tokens": 6000
            },
            "label": "Draft CV alternatives (on base CV)"
        },
        {
            "id": "task-4",
            "kind": "task",
            "config": {
                "brain": "ats-evaluator",
                "prompt": "Score each CV alternative for ATS/keyword match against the brief. Return the best alternative and its gaps.",
                "max_tokens": 1200
            },
            "label": "ATS + keyword scoring"
        },
        {
            "id": "gate-1",
            "kind": "gate",
            "config": {
                "type": "verify",
                "spec": {
                    "type": "regex",
                    "pattern": "."
                }
            },
            "label": "ATS threshold"
        },
        {
            "id": "task-5",
            "kind": "task",
            "config": {
                "brain": "critic",
                "prompt": "Review the best CV as a skeptical hiring manager. List every reason it might be rejected and how to fix each.",
                "max_tokens": 1500
            },
            "label": "Hiring-manager critique"
        },
        {
            "id": "task-6",
            "kind": "task",
            "config": {
                "brain": "finalizer",
                "prompt": "Merge the strongest alternative with the critique fixes into one final, impossible-to-reject CV for {company}. Stay grounded in the base CV below (do not invent experience): optimize and strengthen it. PRESERVE its structure, sections and LaTeX formatting, target ~1.5 pages and NEVER exceed 2 pages (2 is a hard maximum, aim a little under) -- if it does not fit, tighten wording and cut the LEAST relevant bullets and the oldest/least-relevant roles FIRST (never touch contact details or the most relevant experience). Output the complete final CV as valid, compilable LaTeX only.\n\n--- BASE CV (cv_largo.tex) ---\n{base_cv}\n--- END BASE CV --- Output ONLY the complete LaTeX document, starting with \\documentclass and ending with \\end{document} — no prose, no explanations, no markdown fences. HARD RULES: copy ALL contact details (name, email, phone, links, location) EXACTLY and verbatim from the base CV — never alter, reformat, or invent them. Do NOT invent or add tools, technologies, employers, dates, metrics or experience that are not already in the base CV — only rephrase, reorder and emphasize what genuinely exists there.",
                "max_tokens": 8000
            },
            "label": "Synthesize the final CV (LaTeX)"
        },
        {
            "id": "gate-2",
            "kind": "gate",
            "config": {
                "type": "verify",
                "spec": {
                    "type": "contact_preserved"
                },
                "label": "Impossible-to-reject bar"
            },
            "label": "Impossible-to-reject bar"
        },
        {
            "id": "output-1",
            "kind": "output",
            "config": {
                "type": "tex",
                "target": "cv"
            },
            "label": "Export tailored CV (.tex)"
        }
    ],
    "edges": [
        {
            "from_node": "trigger-1",
            "to_node": "task-1"
        },
        {
            "from_node": "task-1",
            "to_node": "task-2"
        },
        {
            "from_node": "task-2",
            "to_node": "task-3"
        },
        {
            "from_node": "task-3",
            "to_node": "task-4"
        },
        {
            "from_node": "task-4",
            "to_node": "gate-1"
        },
        {
            "from_node": "gate-1",
            "to_node": "task-5"
        },
        {
            "from_node": "task-5",
            "to_node": "task-6"
        },
        {
            "from_node": "task-6",
            "to_node": "gate-2"
        },
        {
            "from_node": "gate-2",
            "to_node": "output-1"
        }
    ]
}'''


def _resolve_config(config: "Optional[Config]") -> "Config":
    if config is not None:
        return config
    from lhassa.config import Config as _Config

    return _Config()


def _canonical_flow(flow_id: str) -> Flow:
    """Build the canonical CV :class:`Flow`, with its id set to ``flow_id``."""
    data = json.loads(_CV_FLOW_JSON)
    data["id"] = flow_id
    return Flow.from_dict(data).validate()


def _ensure_brain(
    node_id: str,
    *,
    role: str,
    parent: "Optional[str]",
    department: "Optional[str]" = None,
    skills: tuple[str, ...] = (),
    config: "Optional[Config]" = None,
) -> bool:
    """Create ``node_id`` if it is missing. Return ``True`` if created, else ``False``.

    Idempotent: an existing brain is left untouched (never re-created, never dropped).
    ``parent=None`` attaches under the permanent administrator (Lhassa) via the org
    management API.
    """
    if load_topology(config).has(node_id):
        return False
    management.add_brain(
        node_id,
        role=role,
        parent=parent,
        department=department,
        skills=skills,
        config=config,
    )
    return True


def _ensure_placeholder_base_cv() -> str:
    """Write the placeholder ``cv_largo.tex`` ONLY if none exists. Never destructive.

    Returns ``"kept"`` when a base CV already exists (the owner's real one is left
    untouched) or ``"placeholder"`` when the placeholder was written.
    """
    path = cv_paths.base_path()
    if path.exists():
        return "kept"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_PLACEHOLDER_BASE_CV, encoding="utf-8")
    return "placeholder"


def seed_cv_pipeline(
    config: "Optional[Config]" = None,
    *,
    flow_id: "Optional[str]" = None,
) -> dict:
    """Recreate the CV pipeline on a fresh env. Idempotent + non-destructive.

    Ensures the ``general`` coordinator, the ``interns`` department and its 6 worker
    brains exist (skipping any that already do), wires ``interns`` as the environment
    monitor, (re)writes the canonical CV work-flow, and seeds a placeholder base CV
    only when none exists.

    ``flow_id`` (or ``config.cv_flow_id``) overrides the work-flow id; it defaults to
    :data:`DEFAULT_CV_FLOW_ID`. Never raises destructively — each stage is isolated and
    a failure is recorded under ``errors`` in the returned summary::

        {brains_created, brains_existing, flow_id, monitor, base_cv, errors}
    """
    cfg = _resolve_config(config)
    resolved_flow_id = (
        flow_id or getattr(cfg, "cv_flow_id", None) or DEFAULT_CV_FLOW_ID
    )

    summary: dict = {
        "brains_created": [],
        "brains_existing": [],
        "flow_id": resolved_flow_id,
        "monitor": INTERNS_ID,
        "base_cv": "kept",
        "errors": [],
    }

    # 1. Org scaffold: general → interns → the 6 worker brains (each idempotent).
    try:
        _ensure_brain(GENERAL_ID, role="general", parent=None, config=cfg)
        _ensure_brain(
            INTERNS_ID,
            role="department",
            parent=GENERAL_ID,
            department=INTERNS_DEPARTMENT,
            skills=("research",),
            config=cfg,
        )
        for brain_id in CV_WORKER_BRAINS:
            created = _ensure_brain(
                brain_id,
                role="leaf",
                parent=INTERNS_ID,
                department=INTERNS_DEPARTMENT,
                config=cfg,
            )
            key = "brains_created" if created else "brains_existing"
            summary[key].append(brain_id)
    except Exception as exc:  # partial failure is reported, not fatal
        logger.warning("seed[cv]: org scaffold failed: {}", exc)
        summary["errors"].append(f"org: {type(exc).__name__}: {exc}")

    # 2. Make the interns department the environment monitor/supervisor.
    try:
        result = make_brain_monitor(INTERNS_ID, config=cfg)
        if not result.get("ok"):
            summary["errors"].append(f"monitor: {result.get('reason')}")
    except Exception as exc:
        logger.warning("seed[cv]: monitor wiring failed: {}", exc)
        summary["errors"].append(f"monitor: {type(exc).__name__}: {exc}")

    # 3. Create/overwrite the canonical CV work-flow DEFINITION (code-owned config).
    try:
        FlowStore(cfg).save(_canonical_flow(resolved_flow_id))
    except Exception as exc:
        logger.warning("seed[cv]: flow save failed: {}", exc)
        summary["errors"].append(f"flow: {type(exc).__name__}: {exc}")

    # 4. Placeholder base CV ONLY if none exists (never overwrite the real one).
    try:
        summary["base_cv"] = _ensure_placeholder_base_cv()
    except Exception as exc:
        logger.warning("seed[cv]: base CV placeholder failed: {}", exc)
        summary["errors"].append(f"base_cv: {type(exc).__name__}: {exc}")

    return summary


@register_seed(
    "cv",
    "Recreate the 'Impossible-to-reject CV' pipeline: the interns department, its 6 "
    "worker brains, the monitor role, the canonical CV work-flow, and a placeholder "
    "base CV (never overwriting a real one).",
)
def apply(config: "Optional[Config]" = None) -> dict:
    """The ``cv`` seed entry point — see :func:`seed_cv_pipeline`."""
    return seed_cv_pipeline(config)
