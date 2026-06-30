"""A crash-safe JSON store for work-flows, keyed by id, over ``config.flows_file``.

The file is a single JSON object mapping ``flow_id`` → flow dict. The store keeps
the whole map in the file (small, human-readable, diff-friendly) and writes it
ATOMICALLY: it serialises to a sibling temp file and ``os.replace``\\ s it over the
target, so an interrupted or failing write can never leave a half-written, corrupt
flows file — the previous good file stays intact.

Reads tolerate a missing file (fresh install → no flows) and degrade a malformed
file to "no flows" rather than crashing the caller.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from loguru import logger

from lhassa.config import Config
from lhassa.flows.model import Flow, FlowValidationError


class FlowStore:
    """CRUD over the JSON map of work-flows at ``config.flows_file``."""

    def __init__(self, config: Optional[Config] = None) -> None:
        if config is None:
            # Import locally so a test that reloads ``lhassa.config`` still gets the
            # live class (a stale module-level binding would read the real default
            # path) — the same pattern as ``org.topology._resolve_config``.
            from lhassa.config import Config as _Config

            config = _Config()
        self.config = config
        self.path = Path(getattr(self.config, "flows_file", "") or "")

    # ── reads ──────────────────────────────────────────────────────────────────
    def _read_map(self) -> dict:
        """The raw id→flow-dict map; ``{}`` for a missing/empty/corrupt file."""
        if not self.path or not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("flows: could not read {} ({}); treating as empty", self.path, exc)
            return {}
        if not isinstance(raw, dict):
            logger.warning("flows: {} is not a JSON object; treating as empty", self.path)
            return {}
        return raw

    def list_flows(self) -> list[Flow]:
        """Every stored flow (skipping any individual entry that won't parse)."""
        out: list[Flow] = []
        for fid, data in self._read_map().items():
            try:
                flow = Flow.from_dict(data)
                if not flow.id:
                    flow.id = str(fid)
                out.append(flow)
            except FlowValidationError as exc:
                logger.warning("flows: skipping unparseable flow {!r}: {}", fid, exc)
        return out

    def get(self, flow_id: str) -> Optional[Flow]:
        """The flow with ``flow_id``, or ``None`` if it does not exist."""
        data = self._read_map().get(flow_id)
        if data is None:
            return None
        flow = Flow.from_dict(data)
        if not flow.id:
            flow.id = flow_id
        return flow

    # ── writes ──────────────────────────────────────────────────────────────────
    def _write_map(self, flows: dict) -> None:
        """Atomically replace the flows file with ``flows`` (temp file + os.replace)."""
        if not self.path:
            raise FlowValidationError("no flows_file configured to save to")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(flows, ensure_ascii=False, indent=2) + "\n"
        # Write to a temp file in the SAME directory so os.replace is atomic (same fs).
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".flows-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            # Never leave the temp artefact behind; the original file is untouched.
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise

    def save(self, flow: Flow) -> Flow:
        """Validate and persist ``flow`` (insert or overwrite by id). Returns it."""
        flow.validate()
        flows = self._read_map()
        flows[flow.id] = flow.to_dict()
        self._write_map(flows)
        return flow

    # ``create`` is an alias that fails if the id already exists (POST semantics).
    def create(self, flow: Flow) -> Flow:
        """Persist a NEW flow; raise if its id is already taken."""
        flow.validate()
        flows = self._read_map()
        if flow.id in flows:
            raise FlowValidationError(f"a flow with id {flow.id!r} already exists")
        flows[flow.id] = flow.to_dict()
        self._write_map(flows)
        return flow

    def delete(self, flow_id: str) -> bool:
        """Remove ``flow_id``; return ``True`` if it existed, ``False`` otherwise."""
        flows = self._read_map()
        if flow_id not in flows:
            return False
        del flows[flow_id]
        self._write_map(flows)
        return True
