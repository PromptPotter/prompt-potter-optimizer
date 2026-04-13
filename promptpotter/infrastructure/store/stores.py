"""Concrete store implementations — BackendStore, PlanStore, SessionStore.

Consolidated from individual modules. These are simple file-based stores
that follow identical patterns (EntityStore base or standalone CRUD).
Larger stores (CampaignStore, DatasetRunStore) remain in their own modules.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from promptpotter.domain.backend import BackendConnection, Execution
from promptpotter.infrastructure.store.base import (
    EntityStore,
    read_json,
    read_json_optional,
    validate_path_component,
    write_json,
)

logger = logging.getLogger(__name__)


@dataclass
class ReusablePlanMatch:
    """Result of a smart-search plan reuse lookup.

    ``data`` is the raw on-disk plan dict (ready for ``deserialize_adaptive_recon_plan``).
    ``kind`` tells the caller how to post-process it:

    - ``complete``  — scan finished; reuse ``recon_results.axis_profiles`` directly.
    - ``partial``   — scan interrupted; caller rebuilds profiles from ``recon_results.rows``.
    - ``sibling``   — another plan with matching variant_library_hash had scan data;
                       reuse its baseline/diagnostic/profiles under the current ``plan_id``.
    - ``diagnostic_only`` — plan existed but has no usable scan data.
    """

    kind: Literal["complete", "partial", "sibling", "diagnostic_only"]
    data: dict[str, Any]


class PlanStore(EntityStore):
    """File I/O for smart search plan persistence and resume."""

    def __init__(self, base_dir: Path):
        super().__init__(base_dir, "adaptive_recon_plans")

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
        """Return summary metadata for all smart search plans on disk."""
        plans_dir = self._entity_dir(backend_id)
        if not plans_dir.exists():
            return []
        results = []
        for path in sorted(plans_dir.glob("ssplan_*.json")):
            data = read_json(path)
            config = data.get("config", {})
            scan = data.get("recon_results", {})
            results.append(
                {
                    "plan_id": data["plan_id"],
                    "status": data["status"],
                    "n_diagnostic": config.get("n_diagnostic", "?"),
                    "max_rounds": config.get("max_rounds", "?"),
                    "n_axis_profiles": len(scan.get("axis_profiles", [])),
                    "variant_library_hash": data.get("variant_library_hash", ""),
                }
            )
        return results

    def find_reusable_plan(self, backend_id: str, plan_id: str) -> ReusablePlanMatch | None:
        """Look up a reusable plan for ``plan_id``.

        Preference order: complete scan for this exact plan_id → partial scan
        for this plan_id → sibling plan with matching ``variant_library_hash``
        and scan data → diagnostic-only fallback. Returns ``None`` if no plan
        is on disk for ``plan_id``.
        """
        existing = self.load(backend_id, plan_id)
        if existing is None:
            return None

        status = existing.get("status", "?")
        scan = existing.get("recon_results") or {}
        if status in ("scan_complete", "search_complete") and scan:
            return ReusablePlanMatch(kind="complete", data=existing)
        if status == "scan_partial" and scan:
            return ReusablePlanMatch(kind="partial", data=existing)

        # diagnostic_built → prefer a sibling plan that already has scan data
        vl_hash = existing.get("variant_library_hash", "")
        current_n_diag = existing.get("config", {}).get("n_diagnostic", 6)
        siblings = [
            s
            for s in self.list_all(backend_id)
            if s["plan_id"] != plan_id
            and s["status"] in ("scan_complete", "search_complete")
            and s.get("variant_library_hash") == vl_hash
            and s.get("n_axis_profiles", 0) > 0
        ]
        if siblings:
            siblings.sort(
                key=lambda s: (
                    s.get("n_diagnostic") != current_n_diag,
                    s["status"] != "scan_complete",
                )
            )
            sib_data = self.load(backend_id, siblings[0]["plan_id"])
            if sib_data is not None:
                return ReusablePlanMatch(kind="sibling", data=sib_data)

        return ReusablePlanMatch(kind="diagnostic_only", data=existing)


class BackendStore:
    """File I/O for backend registration and synced API responses."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _backend_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id

    def _sync_dir(self, backend_id: str) -> Path:
        return self._backend_dir(backend_id) / "sync"

    # -- backend CRUD ---------------------------------------------------------

    def register(self, backend: BackendConnection) -> Path:
        """Write backend.json for a new backend."""
        path = self._backend_dir(backend.id) / "backend.json"
        write_json(path, backend.model_dump())
        return path

    def get(self, backend_id: str) -> BackendConnection | None:
        """Read backend.json, return None if not found."""
        data = read_json_optional(self._backend_dir(backend_id) / "backend.json")
        return BackendConnection(**data) if data is not None else None

    def list_all(self) -> list[BackendConnection]:
        """List all registered backends."""
        if not self._base_dir.exists():
            return []
        backends = []
        for d in sorted(self._base_dir.iterdir()):
            cfg = d / "backend.json"
            if cfg.exists():
                backends.append(BackendConnection(**read_json(cfg)))
        return backends

    def update(self, backend: BackendConnection) -> None:
        """Overwrite backend.json with updated data."""
        path = self._backend_dir(backend.id) / "backend.json"
        write_json(path, backend.model_dump())

    # -- sync (verbatim API responses) ----------------------------------------

    def save_sync(self, backend_id: str, key: str, data: Any) -> Path:
        """Store a verbatim API response under sync/.

        ``key`` is a relative path like ``experiments.json`` or
        ``experiments/{id}.json``.
        """
        path = self._sync_dir(backend_id) / key
        write_json(path, data)
        return path

    def load_sync(self, backend_id: str, key: str) -> Any | None:
        """Read a synced API response. Returns None if not found."""
        return read_json_optional(self._sync_dir(backend_id) / key)

    def list_synced_experiments(self, backend_id: str) -> list[dict[str, Any]]:
        """List individual synced experiment files."""
        exp_dir = self._sync_dir(backend_id) / "experiments"
        if not exp_dir.exists():
            return []
        return [read_json(p) for p in sorted(exp_dir.glob("*.json"))]

    # -- executions (absorbed from ExecutionStore) ----------------------------

    def _executions_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id / "executions"

    def load_execution(self, backend_id: str, execution_id: str) -> Execution | None:
        """Load an execution by ID. Returns None if not found."""
        data = read_json_optional(
            self._executions_dir(backend_id) / f"{execution_id}.json",
        )
        return Execution(**data) if data is not None else None

    def list_executions(self, backend_id: str) -> list[dict[str, Any]]:
        """List execution summaries (without full results array)."""
        d = self._executions_dir(backend_id)
        if not d.exists():
            return []
        items = []
        for p in sorted(d.glob("*.json")):
            data = read_json(p)
            items.append(
                {
                    "execution_id": data["execution_id"],
                    "backend_id": data["backend_id"],
                    "experiment_id": data["experiment_id"],
                    "variant_label": data.get("variant_label", ""),
                    "pipeline_notation": data.get("pipeline_notation", ""),
                    "query_count": data.get("query_count", 0),
                    "successful_count": data.get("successful_count", 0),
                    "created_at": data.get("created_at", ""),
                }
            )
        return items

    # -- datasets (absorbed from DatasetStore) --------------------------------

    def _datasets_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id / "datasets"

    def save_dataset(
        self,
        backend_id: str,
        name: str,
        items: list[dict],
        *,
        source_file: str = "",
    ) -> Path:
        """Write a named dataset to disk."""
        validate_path_component(name)
        data: dict[str, Any] = {
            "name": name,
            "created_at": datetime.now(UTC).isoformat(),
            "source_file": source_file,
            "row_count": len(items),
            "items": items,
        }
        path = self._datasets_dir(backend_id) / f"{name}.json"
        write_json(path, data)
        return path

    def load_dataset(self, backend_id: str, name: str) -> dict[str, Any] | None:
        """Load a named dataset. Returns ``None`` if not found."""
        validate_path_component(name)
        return read_json_optional(
            self._datasets_dir(backend_id) / f"{name}.json",
        )

    # -- connector profile (persistent per-backend defaults) -------------------

    def save_connector_profile(self, backend_id: str, profile: dict[str, Any]) -> None:
        """Write connector profile — persistent campaign defaults for this backend."""
        path = self._backend_dir(backend_id) / "connector_profile.json"
        write_json(path, profile)

    def load_connector_profile(self, backend_id: str) -> dict[str, Any] | None:
        """Load connector profile. Returns None if no profile saved."""
        return read_json_optional(
            self._backend_dir(backend_id) / "connector_profile.json",
        )


# Thin pointer to the active session — survives across CLI invocations
_ACTIVE_SESSION_PATH = Path(".promptpotter") / "active_session.json"


def generate_session_id() -> str:
    """Generate a unique session identifier."""
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:6]
    return f"{ts}_{short}"


class SessionStore:
    """File I/O for campaign session state."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _session_dir(self, backend_id: str, session_id: str) -> Path:
        validate_path_component(backend_id)
        validate_path_component(session_id)
        return self._base_dir / backend_id / "sessions" / session_id

    # -- Session state ---------------------------------------------------------

    def create(self, backend_id: str, state: dict[str, Any]) -> str:
        """Create a new session. Returns session_id."""
        session_id = generate_session_id()
        state["session_id"] = session_id
        state["created_at"] = datetime.now(UTC).isoformat()
        self.save(backend_id, session_id, state)
        return session_id

    def save(self, backend_id: str, session_id: str, state: dict[str, Any]) -> None:
        """Persist session state."""
        path = self._session_dir(backend_id, session_id) / "session.json"
        write_json(path, state)

    def load(self, backend_id: str, session_id: str) -> dict[str, Any] | None:
        """Load session state. Returns None if not found."""
        path = self._session_dir(backend_id, session_id) / "session.json"
        return read_json_optional(path)

    # -- Scan results ----------------------------------------------------------

    def save_recon_results(
        self,
        backend_id: str,
        session_id: str,
        scan_df_records: list[dict],
        axis_profiles: list[dict],
    ) -> None:
        """Persist sensitivity scan results (fills the persistence gap)."""
        path = self._session_dir(backend_id, session_id) / "recon_results.json"
        write_json(
            path,
            {
                "recon_df": scan_df_records,
                "axis_profiles": axis_profiles,
                "saved_at": datetime.now(UTC).isoformat(),
            },
        )

    def load_recon_results(
        self,
        backend_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        """Load scan results. Returns None if not found."""
        path = self._session_dir(backend_id, session_id) / "recon_results.json"
        return read_json_optional(path)

    # -- Campaign log ----------------------------------------------------------

    def append_log(
        self,
        backend_id: str,
        session_id: str,
        section: str,
    ) -> None:
        """Append a markdown section to the campaign log."""
        path = self._session_dir(backend_id, session_id) / "campaign_log.md"
        with open(path, "a", encoding="utf-8") as f:
            f.write(section + "\n\n")

    def load_log(self, backend_id: str, session_id: str) -> str:
        """Load the full campaign log. Returns empty string if not found."""
        path = self._session_dir(backend_id, session_id) / "campaign_log.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    # -- Active session pointer ------------------------------------------------

    def save_active_pointer(self, backend_id: str, session_id: str) -> None:
        """Persist pointer to the active session across CLI invocations."""
        _ACTIVE_SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ACTIVE_SESSION_PATH.write_text(
            json.dumps({"backend_id": backend_id, "session_id": session_id}),
            encoding="utf-8",
        )

    def clear_active_pointer(self) -> None:
        """Delete the active-session pointer file, if present. Idempotent."""
        _ACTIVE_SESSION_PATH.unlink(missing_ok=True)

    def read_active_pointer(self) -> tuple[str, str]:
        """Return ``(backend_id, session_id)`` from the pointer, or ``("", "")``.

        Unlike :meth:`load_active`, this does NOT raise if the pointer is missing
        or the referenced session has been deleted — it only inspects the raw
        pointer file. Used by guardrails that need to compare the pointer to a
        requested backend without coupling to session lifecycle.
        """
        if not _ACTIVE_SESSION_PATH.exists():
            return "", ""
        try:
            ptr = json.loads(_ACTIVE_SESSION_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "", ""
        return ptr.get("backend_id", ""), ptr.get("session_id", "")

    def load_active(
        self,
        session_override: str | None = None,
    ) -> tuple[dict[str, Any], str, str]:
        """Load active session state + backend_id + session_id.

        Reads ``.promptpotter/active_session.json`` to find the active
        session, then loads the full session state.

        Raises ``SystemExit`` if no active session or session not found.
        """
        if not _ACTIVE_SESSION_PATH.exists():
            raise SystemExit("ERROR: No active session. Run 'init' first.")
        ptr = json.loads(_ACTIVE_SESSION_PATH.read_text(encoding="utf-8"))
        bid = ptr["backend_id"]
        sid = session_override or ptr["session_id"]
        state = self.load(bid, sid)
        if not state:
            raise SystemExit(f"ERROR: Session '{sid}' not found for backend '{bid}'.")
        return state, bid, sid
