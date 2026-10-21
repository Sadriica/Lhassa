# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deliver seam; "Lhassa is the sender of record" (§org: the hierarchy is the
philosophy, not a feature).

:mod:`lhassa.org.delegate` is the DOWN-hop exemplar: work flows down the chart,
one deterministic $0 Episode narrates the handoff, and a topology failure never
blocks the work. This module is the mirror UP-hop: a leaf brain (a department)
produces a work product; a tailored CV (``.tex`` + compiled ``.pdf``), a report,
any file the owner ends up receiving; and that artifact travels back UP the
SAME command chain to the permanent root administrator (Lhassa), which is the
one and only sender of record. Every brain on the chain; the department, any
intermediate coordinator, and the root; logs its OWN hop, so the full audit
trail (department → … → Lhassa → owner) is reconstructable from the episode log
alone, exactly like the down direction.

Two design rules mirror :mod:`lhassa.org.delegate` exactly:

  * **Deterministic + $0, never an LLM.** The chain is read off the
    :class:`~lhassa.org.topology.Topology` tree via the same
    :func:`~lhassa.org.delegate.command_path` the down-hop uses; no model call.
  * **Audit is best-effort, delivery is not.** Registering the artifact and
    logging the per-brain chain are wrapped so ANY failure (a broken topology, a
    DB error) degrades gracefully; a broken/unknown department collapses to a
    single root-attributed hop; but the actual SEND always happens. The only
    thing that can make :func:`deliver` report a failure is the send itself.

:func:`deliver` never raises.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from loguru import logger

from lhassa.config import Config
from lhassa.core import db
from lhassa.core.episode import Episode
from lhassa.org import delegate

# The trigger label for a delivery-chain episode; greppable alongside
# ``org-delegate`` on the same audit plane the monitor reads.
DESPERTAR_DELIVER = "org-deliver"


# ── small, pure narration helpers ─────────────────────────────────────────────


def _file_names(paths: list) -> str:
    """A short, human ``a.tex, a.pdf`` listing of file basenames."""
    names = [Path(p).name for p in (paths or []) if str(p).strip()]
    return ", ".join(names) if names else "no files"


def _metadata_summary(metadata: Optional[dict]) -> str:
    """A short ``k=v, k=v`` rendering of ``metadata`` (insertion order, capped)."""
    if not metadata:
        return ""
    parts = [f"{k}={v}" for k, v in metadata.items()]
    return ", ".join(parts[:6])


def _send_name(document: Optional[Union[str, Path]], paths: list) -> str:
    """The file name that best represents "what was delivered": a compiled
    ``.pdf`` wins over the ``.tex`` source; falls back to the document itself,
    else ``"message"`` for a text-only delivery."""
    for p in paths or []:
        if str(p).lower().endswith(".pdf"):
            return Path(p).name
    if document:
        return Path(document).name
    return "message"


def _channel_for(document: Optional[Union[str, Path]], config: Config) -> str:
    """The delivery channel name used in the episode narration.

    A document goes out on the file-capable channel
    (:func:`lhassa.notify.channels.document_channel`: ``telegram`` when the
    telegram pack is installed, ``none`` when no channel can carry a file); a
    text fans out over every active channel.
    """
    if document:
        try:
            from lhassa.notify.channels import NO_DOCUMENT_CHANNEL, document_channel

            channel = document_channel(config)
            return getattr(channel, "name", "?") if channel is not None else NO_DOCUMENT_CHANNEL
        except Exception:
            return "none"
    try:
        from lhassa.notify.channels import active_channels

        names = [c.name for c in active_channels(config)]
    except Exception:
        names = []
    return "+".join(names) if names else "log"


# ── artifact registration (best-effort; audit, never blocks the send) ───────


async def _register_artifact(
    instance_id: str,
    *,
    kind: str,
    paths: list,
    metadata: Optional[dict],
    origin_department: str,
) -> Optional[str]:
    """Register the artifact, best-effort. Returns the id, or ``None`` on failure."""
    if not paths:
        return None
    try:
        from lhassa.core import artifacts

        return await artifacts.register(
            instance_id,
            kind=kind,
            paths=paths,
            metadata=metadata,
            origin_department=origin_department,
        )
    except Exception as exc:  # audit is best-effort; never blocks the send
        logger.debug("org-deliver: artifact registration failed: {}", exc)
        return None


# ── the per-brain chain walk (one Episode per hop, LLM-free, $0) ─────────────


def _resolve_chain(origin_department: str, config: Optional[Config]) -> list[str]:
    """The command chain from the root admin down to ``origin_department``.

    Reuses :func:`lhassa.org.delegate.command_path` (root → … → department); an
    empty/unknown department, or a topology that fails to load, degrades to a
    single root-attributed hop (never an empty chain, never a raise).
    """
    try:
        from lhassa.org import topology as topo_mod

        topology = topo_mod.load_topology(config)
        return delegate.command_path(topology, origin_department)
    except Exception as exc:
        logger.debug("org-deliver: topology unavailable, degrading to root hop: {}", exc)
        try:
            from lhassa.org.topology import admin_id

            return [admin_id(config)]
        except Exception:
            return ["lhassa"]


async def _log_chain(
    chain: list[str],
    *,
    origin_department: str,
    file_names: str,
    metadata_summary: str,
    artifact_id: Optional[str],
    send_name: str,
    channel: str,
    instance_id: str,
) -> None:
    """Write ONE Episode per brain on ``chain``, walking leaf → root.

    Each hop is best-effort: a single write failing is logged and skipped so it
    never breaks the rest of the chain (or the send that follows).
    """
    up = list(reversed(chain))  # leaf (origin) .. root
    for i, brain_id in enumerate(up):
        is_root = i == len(up) - 1
        try:
            if not is_root:
                parent_id = up[i + 1]
                # The origin brain PRODUCED the work; brains above RECEIVED it.
                if i == 0:
                    parts = [f"produced: {file_names}"]
                else:
                    parts = [f"received from {up[i - 1]}: {file_names}"]
                if metadata_summary:
                    parts.append(metadata_summary)
                parts.append(f"forwarding ↑ {parent_id}")
                if artifact_id:
                    parts.append(f"artifact {artifact_id}")
                resultado = " · ".join(parts)
                decision = f"{brain_id} forwards the delivery up to {parent_id}"
                accion = f"forward:{brain_id}→{parent_id}"
            else:
                child = up[i - 1] if i > 0 else (origin_department or "flow")
                parts = [f"received from {child}"]
                if artifact_id:
                    parts.append(f"artifact {artifact_id}")
                parts.append(f"delivering {send_name} → owner via {channel}")
                resultado = " · ".join(parts)
                decision = f"{brain_id} delivers {send_name} to the owner"
                accion = f"deliver:{brain_id}→owner"

            ep = Episode.begin(despertar_por=DESPERTAR_DELIVER, instance_id=instance_id)
            ep.dominio = brain_id
            ep.finish(decision=decision, accion=accion, resultado=resultado, costo_usd=0.0)
            await ep.append()
        except Exception as exc:  # audit is best-effort; never blocks the send
            logger.debug("org-deliver: hop episode for {} failed: {}", brain_id, exc)


async def _log_final(
    *, root_id: str, send_name: str, channel: str, ok: bool, instance_id: str
) -> None:
    """The final root episode recording the actual send OUTCOME (honest on failure)."""
    try:
        status = "ok" if ok else "failed"
        ep = Episode.begin(despertar_por=DESPERTAR_DELIVER, instance_id=instance_id)
        ep.dominio = root_id
        ep.finish(
            decision=f"{root_id} confirms delivery of {send_name}",
            accion=f"delivered:{send_name}",
            resultado=f"delivered: {send_name} → owner ({channel}, {status})",
            costo_usd=0.0,
        )
        await ep.append()
    except Exception as exc:  # audit is best-effort; never blocks the send
        logger.debug("org-deliver: final episode failed: {}", exc)


# ── the seam callers go through ───────────────────────────────────────────────


async def deliver(
    text: str = "",
    *,
    document: Optional[Union[str, Path]] = None,
    paths: Optional[list] = None,
    caption: str = "",
    kind: str = "message",
    metadata: Optional[dict] = None,
    origin_department: str = "",
    config: Optional[Config] = None,
) -> tuple[bool, Optional[str]]:
    """Deliver a work product (or a plain message) UP the chain to the owner.

    Lhassa is the sender of record: whatever a department produces travels back
    up the SAME command chain :mod:`lhassa.org.delegate` used to hand work down,
    and every brain on that chain; the department, any intermediate
    coordinator, and the root (Lhassa); logs its own ``org-deliver`` Episode
    narrating its hop, before the root actually sends. Order of operations:

      1. **Register the artifact** (best-effort) when ``document`` is given;
         ``paths`` defaults to ``[document]`` when not supplied explicitly (e.g.
         a CV's ``.tex`` source travelling WITH its compiled ``.pdf``). Failure
         here is logged and swallowed; the rest of the flow proceeds with
         ``artifact_id=None``.
      2. **Walk the chain UP, one Episode per brain**, from the department that
         produced it to the root admin (Lhassa): the department's hop narrates
         what it received and that it is forwarding up; any intermediate
         coordinator narrates the same forward; the root's hop narrates
         receiving it and its INTENT to deliver. A topology failure (or an
         empty/unknown ``origin_department``) degrades to a single
         root-attributed hop; it never blocks the send.
      3. **Send**: a ``document`` goes out via
         :func:`lhassa.notify.channels.deliver_document` (the file-capable
         channel, Telegram when its pack is installed; with none an honest
         failure, never a raise), its ``caption``
         routed through :func:`lhassa.notify.voice.revoice` first (the
         document path used to bypass the voice layer entirely; closed); a
         plain ``text`` (no document) fans out via
         :func:`lhassa.notify.dispatch.notify`, which already revoices.  Both
         are the existing config-gated seams; an unconfigured channel simply
         doesn't deliver.
      4. **Final root episode** recording the actual send OUTCOME; honest on
         failure (``"...({channel}, failed)"``), never spun as a success.

    THE GOLDEN RULE: audit is best-effort, delivery is not. Any failure in step
    1 or 2 (the artifact write, the topology walk, an episode write) is logged
    and swallowed; the send in step 3 still happens. :func:`deliver` itself
    NEVER raises; it returns ``(False, artifact_id)`` only when the SEND
    (step 3) failed, and ``(True, artifact_id)`` otherwise. ``artifact_id`` is
    ``None`` whenever no document was given, or artifact registration failed.
    """
    if config is None:
        config = Config()
    instance_id = getattr(config, "instance_id", None) or Config.instance_id

    effective_paths = list(paths) if paths is not None else ([document] if document else [])

    artifact_id = await _register_artifact(
        instance_id,
        kind=kind,
        paths=effective_paths,
        metadata=metadata,
        origin_department=origin_department,
    )

    chain = _resolve_chain(origin_department, config)
    file_names = _file_names(effective_paths)
    metadata_summary = _metadata_summary(metadata)
    send_name = _send_name(document, effective_paths)
    channel = _channel_for(document, config)

    await db.init_db()
    await _log_chain(
        chain,
        origin_department=origin_department,
        file_names=file_names,
        metadata_summary=metadata_summary,
        artifact_id=artifact_id,
        send_name=send_name,
        channel=channel,
        instance_id=instance_id,
    )

    delivered = False
    try:
        if document:
            from lhassa.notify import voice as _voice
            from lhassa.notify.channels import deliver_document

            # Close the voice bypass: a document's caption used to reach the
            # owner raw, skipping revoice entirely; the ONE surface where
            # machine text could still get through. Route it through the same
            # $0 voice layer text-only deliveries already get.
            voice_kind = kind if kind in _voice.KINDS else None
            voiced_caption = await _voice.revoice(caption, kind=voice_kind, config=config)
            delivered, _channel = await deliver_document(
                document, caption=voiced_caption, config=config
            )
        else:
            from lhassa.notify import voice as _voice
            from lhassa.notify.dispatch import notify

            # Forward the delivery's kind into the voice layer when it maps to a
            # notification kind (action-result, reminder…) so the message is
            # framed in Lhassa's voice; artifact-only kinds fall back to the
            # sniffer rather than forcing an unknown label.
            voice_kind = kind if kind in _voice.KINDS else None
            delivered = await notify(text, level="info", config=config, kind=voice_kind)
    except Exception as exc:  # the send seams already never raise, but be sure
        logger.warning("org-deliver: send failed: {}", exc)
        delivered = False

    root_id = chain[0] if chain else getattr(config, "root_admin_id", "lhassa")
    await _log_final(
        root_id=root_id,
        send_name=send_name,
        channel=channel,
        ok=delivered,
        instance_id=instance_id,
    )

    return delivered, artifact_id
