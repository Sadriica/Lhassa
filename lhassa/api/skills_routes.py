"""Skills routes — first-class SKILL.md CRUD + AI create/improve + recommend.

CRUD + AI create/improve + offline recommendations over the SkillStore
(~/.lhassa/skills), plus which brains carry a skill. Defensive: bad body → 400,
missing skill → 404, AI degrades to a clear error when no model is configured.
Static sub-paths (recommend) are declared before the ``{name}`` routes so they win.

Mounted by the integrator with ``app.include_router(skills_routes.router)``.
"""

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from lhassa.config import Config

router = APIRouter()


def _skill_store():
    from lhassa.skills.store import SkillStore

    return SkillStore(Config)


@router.get("/api/skills")
async def list_skills_endpoint():
    """Every installed skill (SKILL.md) as JSON."""
    from lhassa.skills.skillmd import skill_to_dict

    return [skill_to_dict(s) for s in _skill_store().list_skills()]


@router.get("/api/skills/recommend")
async def recommend_skills_endpoint(context: str = Query(default="")):
    """Recommended skills — the offline catalog minus what's installed."""
    from lhassa.skills.ai import recommend_skills

    return recommend_skills(context or None, config=Config)


@router.post("/api/skills")
async def create_skill_endpoint(payload: dict):
    """Create a skill from ``{name, description?, instructions?, metadata?}``."""
    from lhassa.skills.skillmd import skill_from_dict, skill_to_dict

    if not isinstance(payload, dict) or not (payload.get("name") or "").strip():
        raise HTTPException(status_code=400, detail="'name' is required.")
    try:
        skill = skill_from_dict(payload)
        _skill_store().create(skill)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return skill_to_dict(skill)


@router.post("/api/skills/ai")
async def create_skill_ai_endpoint(payload: dict):
    """Draft a skill WITH AI from ``{brief}`` and save it (degrades without a model)."""
    from lhassa.skills.ai import create_skill_with_ai
    from lhassa.skills.skillmd import skill_to_dict

    brief = (payload or {}).get("brief") if isinstance(payload, dict) else None
    if not brief:
        raise HTTPException(status_code=400, detail="'brief' is required.")
    skill = await create_skill_with_ai(brief, config=Config)
    saved = False
    if not skill.error:
        try:
            _skill_store().save(skill)
            saved = True
        except Exception as exc:  # persisting must not fail the request
            logger.warning("skills: could not save AI-drafted skill: {}", exc)
    return {"skill": skill_to_dict(skill), "saved": saved, "error": skill.error}


@router.get("/api/skills/{name}")
async def get_skill_endpoint(name: str):
    from lhassa.skills.skillmd import skill_to_dict

    skill = _skill_store().get(name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"skill {name!r} not found.")
    return skill_to_dict(skill)


@router.get("/api/skills/{name}/brains")
async def skill_brains_endpoint(name: str):
    """Which brains carry this skill."""
    from lhassa.skills.store import brains_with_skill

    return {"skill": name, "brains": brains_with_skill(name, Config)}


@router.post("/api/skills/{name}/improve")
async def improve_skill_endpoint(name: str):
    """Improve a skill WITH AI and save it (degrades without a model)."""
    from lhassa.skills.ai import improve_skill_with_ai
    from lhassa.skills.skillmd import skill_to_dict

    skill = await improve_skill_with_ai(name, config=Config)
    saved = False
    if not skill.error:
        try:
            _skill_store().save(skill)
            saved = True
        except Exception as exc:
            logger.warning("skills: could not save improved skill: {}", exc)
    return {"skill": skill_to_dict(skill), "saved": saved, "error": skill.error}


@router.patch("/api/skills/{name}")
async def update_skill_endpoint(name: str, payload: dict):
    """Overwrite a skill from a JSON body. Returns the saved skill."""
    from lhassa.skills.skillmd import skill_from_dict, skill_to_dict

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be a JSON object.")
    data = dict(payload)
    data.setdefault("name", name)
    try:
        skill = skill_from_dict(data)
        _skill_store().save(skill)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return skill_to_dict(skill)


@router.delete("/api/skills/{name}")
async def delete_skill_endpoint(name: str):
    if not _skill_store().delete(name):
        raise HTTPException(status_code=404, detail=f"skill {name!r} not found.")
    return {"ok": True, "deleted": name}


@router.post("/api/skills/{name}/assign")
async def assign_skill_endpoint(name: str, payload: dict):
    """Assign a skill to a brain. Body ``{brain_id}``."""
    from lhassa.skills.store import assign_to_brain

    brain_id = (payload or {}).get("brain_id") if isinstance(payload, dict) else None
    if not brain_id:
        raise HTTPException(status_code=400, detail="'brain_id' is required.")
    try:
        assign_to_brain(name, brain_id, Config)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "skill": name, "brain": brain_id}


@router.post("/api/skills/{name}/install")
async def install_skill_endpoint(name: str):
    """Install a skill from the built-in catalog (materializes its SKILL.md).

    Used by the "skill referenced but not installed" affordance: a brain can carry a
    skill NAME, but the skill only does something once installed. Returns the
    installed skill; 404 if the name is not in the catalog (create it with AI instead).
    """
    from lhassa.skills.ai import install_from_catalog
    from lhassa.skills.skillmd import skill_to_dict

    skill = install_from_catalog(name, config=Config)
    if skill is None:
        raise HTTPException(
            status_code=404,
            detail=f"{name!r} is not in the catalog — create it with AI or by hand.",
        )
    return {"ok": True, "skill": skill_to_dict(skill)}
