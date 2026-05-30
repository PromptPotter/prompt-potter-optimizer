"""M12 Control-remote contract — drift invariant.

Bundled assertion of the wire-contract integrity guardrails declared in
`docs/adr/0001-m12-control-plane.md` (security checklist boxes 1-3, 13-15, 20).

Per `tests/CLAUDE.md`: one bundled test, one canonical case per contract — not
N parallel tests. Vacuous-green at Profile -1 (empty closed sets), grows teeth
as Profiles A-E ship and the record/handler symmetry becomes non-trivial.

The two YAML wire-contract files are the source of truth for what the system
can ingress and egress; this test enforces that the YAML files and the Python
code never drift out of sync.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, get_args, get_type_hints

import yaml

from promptpotter.domain import run_records

_REPO_ROOT = Path(__file__).resolve().parent.parent
_OPENAPI_PATH = _REPO_ROOT / "docs" / "specs" / "m12-api-openapi.yaml"
_ASYNCAPI_PATH = _REPO_ROOT / "docs" / "specs" / "m12-events-asyncapi.yaml"
_CONTRACT_PATH = _REPO_ROOT / "docs" / "adr" / "0001-m12-control-plane.md"
_IDENTITY_ADR_PATH = _REPO_ROOT / "docs" / "adr" / "0002-identity-foundation.md"
_SPEND_ADR_PATH = _REPO_ROOT / "docs" / "adr" / "0003-spend-and-tenancy.md"
_ADMIN_ADR_PATH = _REPO_ROOT / "docs" / "adr" / "0004-operator-admin-channels.md"

# Projection-only event kinds — emitted on the SSE channel but have no
# underlying record class in `domain/run_records.py`. Synthesized by
# EventStreamView at Profile A; expand only at profile boundaries.
#   - `stream_snapshot`: the leading snapshot frame (security box 14).
# `command` / `command_ack` are NOT projection-only — they ride records
# in the union (`CommandRecord` / `CommandAckRecord`) and round-trip
# through the canonical ledger.
_PROJECTION_ONLY_KINDS: frozenset[str] = frozenset({"stream_snapshot"})


def _record_type_literals() -> frozenset[str]:
    """Extract the `record_type: Literal[...]` discriminator values from every
    record class on the `CycleRecord` union — the codebase truth for what
    kinds the canonical ledger can carry."""
    cycle_record = run_records.CycleRecord
    # `Annotated[Union[...], Field(discriminator=...)]` — first arg of get_args
    # is the union, whose args are the member classes.
    annotated_args = get_args(cycle_record)
    union_type = annotated_args[0]
    record_classes = get_args(union_type)
    literals: set[str] = set()
    for cls in record_classes:
        hints = get_type_hints(cls)
        rt = hints.get("record_type")
        assert rt is not None, f"{cls.__name__} missing record_type annotation"
        # rt is Literal["..."]; get_args returns the literal values.
        values = get_args(rt)
        assert len(values) == 1, f"{cls.__name__}.record_type must be a single Literal"
        literals.add(values[0])
    return frozenset(literals)


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _anchor_paths(contract_text: str) -> list[str]:
    """Extract file paths from the Anchors table in the ADR.

    The Anchors section may live at any heading depth (MADR places it under
    `## More Information` as `### Anchors`; the previous Markdown-spec shape
    put it at top level as `## Anchors`). Either is accepted; the section
    ends at the next heading of equal-or-lesser depth.

    Each row's second column carries one or more backtick-quoted segments;
    a path is the part before any `::symbol` qualifier. Paths must contain
    a slash (filters out bare backtick-quoted symbol names).
    """
    paths: list[str] = []
    in_section = False
    section_depth = 0
    for line in contract_text.splitlines():
        stripped = line.strip()
        heading_match = _HEADING_RE.match(stripped)
        if heading_match:
            depth = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            if title.lower() == "anchors":
                in_section = True
                section_depth = depth
                continue
            if in_section and depth <= section_depth:
                break
            # Sub-heading inside the Anchors section (rare); skip but stay in section.
            continue
        if not in_section or not stripped.startswith("|"):
            continue
        cols = [c.strip() for c in stripped.split("|")[1:-1]]
        if len(cols) < 2:
            continue
        file_col = cols[1]
        if set(file_col) <= {"-", " "} or file_col.lower() in {"file", ""}:
            continue
        for match in re.finditer(r"`([^`]+)`", file_col):
            raw = match.group(1)
            path = raw.split("::")[0].strip()
            if "/" in path and not path.startswith("http"):
                paths.append(path)
    return paths


def test_control_plane_drift() -> None:
    """Bundled assertion: OpenAPI + AsyncAPI YAMLs are well-formed, the
    declared closed sets stay in sync with the codebase, and every anchor in
    0001-m12-control-plane.md points at a file that exists on disk."""

    # ----- Files exist (Profile -1 scaffold landed) -----
    assert _OPENAPI_PATH.is_file(), f"missing {_OPENAPI_PATH}"
    assert _ASYNCAPI_PATH.is_file(), f"missing {_ASYNCAPI_PATH}"
    assert _CONTRACT_PATH.is_file(), f"missing {_CONTRACT_PATH}"

    # ----- OpenAPI 3.1 wire contract -----
    openapi: dict[str, Any] = yaml.safe_load(_OPENAPI_PATH.read_text(encoding="utf-8"))
    assert openapi.get("openapi", "").startswith("3.1"), (
        f"OpenAPI version must be 3.1.x, got {openapi.get('openapi')!r}"
    )
    info = openapi.get("info") or {}
    assert info.get("title") and info.get("version"), "OpenAPI info.title / info.version required"

    components = openapi.get("components") or {}
    parameters = components.get("parameters") or {}
    schemas = components.get("schemas") or {}
    responses = components.get("responses") or {}

    # Mandatory reusable parameters — the trust-boundary headers
    # (security boxes 8, 9). `Idempotency-Key` is required on every command;
    # `Expected-Version` is required by the Profile B contract but is
    # declared `required: false` at the parameter component for the v0
    # relaxation window (the webapp does not yet thread ledger sequence
    # through mutations — see the v0-relaxation note inside the YAML).
    for name in ("IdempotencyKey", "ExpectedVersion"):
        param = parameters.get(name)
        assert param is not None, f"OpenAPI components.parameters.{name} missing"
        assert param.get("in") == "header", f"{name} must be a header parameter"
    assert parameters["IdempotencyKey"].get("required") is True, (
        "Idempotency-Key parameter must be required on every command (security box 8)."
    )

    # Mandatory reusable schemas — the inbound envelope shape
    # (security boxes 5, 6, 17).
    for schema_name in ("CommandEnvelope", "CommandAcceptedBody", "ErrorEnvelope"):
        assert schema_name in schemas, f"OpenAPI components.schemas.{schema_name} missing"

    # Error code closed set must include the trust-boundary codes.
    error_codes = set((schemas["ErrorEnvelope"]["properties"]["error"]).get("enum") or [])
    for required_code in ("version_conflict", "idempotency_key_missing", "capability_denied"):
        assert required_code in error_codes, (
            f"ErrorEnvelope.error enum must include {required_code!r}"
        )

    # Mandatory reusable responses — including the 409 version_conflict shape.
    for response_name in ("CommandAccepted", "VersionConflict"):
        assert response_name in responses, f"OpenAPI components.responses.{response_name} missing"

    # Handler ↔ schema parity — at Profile -1 both sides are empty.
    # As Profile B lands, this assertion grows real teeth: every declared
    # operation must have a handler in presentation/api/routers/commands.py.
    declared_operations = openapi.get("paths") or {}
    # Profile -1: zero operations. Profile B+: parity check vs handler registry.
    # Until the commands router exists, we only assert paths is a dict.
    assert isinstance(declared_operations, dict), "OpenAPI paths must be a mapping"

    # ----- AsyncAPI 3.0 wire contract -----
    asyncapi: dict[str, Any] = yaml.safe_load(_ASYNCAPI_PATH.read_text(encoding="utf-8"))
    assert asyncapi.get("asyncapi", "").startswith("3.0"), (
        f"AsyncAPI version must be 3.0.x, got {asyncapi.get('asyncapi')!r}"
    )

    channels = asyncapi.get("channels") or {}
    cycle_events = channels.get("cycleEvents")
    assert cycle_events is not None, "AsyncAPI channels.cycleEvents missing"
    assert ":subscribe" in (cycle_events.get("address") or ""), (
        "cycleEvents.address must end in ':subscribe' (snapshot-then-tail SSE channel)"
    )

    schemas_async = ((asyncapi.get("components") or {}).get("schemas")) or {}
    envelope = schemas_async.get("ProjectionEnvelope")
    assert envelope is not None, "AsyncAPI components.schemas.ProjectionEnvelope missing"

    # ProjectionEnvelope shape — security box 13 (closed envelope).
    required_fields = set(envelope.get("required") or [])
    expected_required = {"kind", "version", "cycle_id", "sequence", "payload"}
    assert required_fields == expected_required, (
        f"ProjectionEnvelope.required must be {sorted(expected_required)}, "
        f"got {sorted(required_fields)}"
    )

    # Heartbeat shape — security box 15.
    heartbeat = schemas_async.get("HeartbeatPayload")
    assert heartbeat is not None, "AsyncAPI components.schemas.HeartbeatPayload missing"
    assert "emitted_at" in (heartbeat.get("required") or []), (
        "HeartbeatPayload.emitted_at must be required"
    )

    # ----- Closed outbound set parity: AsyncAPI kind enum vs CycleRecord ----
    declared_kinds = set(envelope["properties"]["kind"].get("enum") or [])
    record_kinds = _record_type_literals()
    missing_from_yaml = record_kinds - declared_kinds
    assert not missing_from_yaml, (
        f"AsyncAPI ProjectionEnvelope.kind enum is missing record_type literals: "
        f"{sorted(missing_from_yaml)}. Declare them in m12-events-asyncapi.yaml "
        f"before adding the record class to CycleRecord."
    )
    unexplained = declared_kinds - record_kinds - _PROJECTION_ONLY_KINDS
    assert not unexplained, (
        f"AsyncAPI declares kinds with no record class and no projection-only "
        f"allowlist entry: {sorted(unexplained)}. Either add the record class "
        f"or add the kind to _PROJECTION_ONLY_KINDS in this test."
    )

    # ----- Profile A: Python ProjectionEnvelope mirrors AsyncAPI shape ----
    from promptpotter.domain.projection_envelope import (
        ProjectionEnvelope,
        ProjectionKind,
    )

    # The YAML's `required` set is the "always-on-the-wire" contract. Each
    # such field must exist in the Python model; pydantic defaults guarantee
    # presence on serialization even when not required at construction time.
    py_fields = set(ProjectionEnvelope.model_fields)
    yaml_required = set(envelope.get("required") or [])
    missing_in_python = yaml_required - py_fields
    assert not missing_in_python, (
        f"AsyncAPI ProjectionEnvelope.required names fields the Python model "
        f"does not declare: {sorted(missing_in_python)}. Update either side."
    )

    # ProjectionKind Literal arms must match the YAML enum exactly.
    py_kinds = set(get_args(ProjectionKind))
    assert py_kinds == declared_kinds, (
        f"ProjectionKind Literal drifted from YAML enum: "
        f"py-only={sorted(py_kinds - declared_kinds)}, "
        f"yaml-only={sorted(declared_kinds - py_kinds)}"
    )

    # ----- Profile A: SSE handler exists at the declared channel address ----
    # AsyncAPI declares the channel at .../events:subscribe. Profile A adds the
    # FastAPI handler; assert the route is registered on `app`.
    from promptpotter.main import app

    channel_address = cycle_events.get("address") or ""
    registered_paths = [getattr(r, "path", "") for r in app.routes]
    expected_route = "/api/v1" + channel_address
    assert any(channel_address in p for p in registered_paths), (
        f"AsyncAPI declares channel at {channel_address!r} but no FastAPI route "
        f"matches; routes registered: {sorted(p for p in registered_paths if 'events' in p)}. "
        f"Expected something like {expected_route!r}."
    )

    # ----- Anchors table integrity (security box 20) ----
    contract_text = _CONTRACT_PATH.read_text(encoding="utf-8")
    anchor_paths = _anchor_paths(contract_text)
    assert anchor_paths, "Anchors table must list at least the Profile -1 entries"
    for raw_path in anchor_paths:
        # Strip section-anchor qualifiers like 'docs/architecture.md#section'.
        path_only = raw_path.split("#")[0]
        candidate = _REPO_ROOT / path_only
        assert candidate.exists(), (
            f"Anchors table references {raw_path!r} which does not exist on disk"
        )

    # Symbolic placate-mypy use of the import (kept narrow so deptry sees usage).
    _ = Literal["pinned-import"]


def test_adr_anchor_files_exist() -> None:
    """Every Anchors-table file path in ADR-0001 / ADR-0002 / ADR-0003 /
    ADR-0004 must exist on disk. ADR-0001 already checks itself inside the
    bundled drift test (security box 20); this asserts the same shape for the
    sibling ADRs whose Anchors tables match the ``| Concern | File |`` format
    and sit under an ``### Anchors`` heading."""
    for adr_path in (_CONTRACT_PATH, _IDENTITY_ADR_PATH, _SPEND_ADR_PATH, _ADMIN_ADR_PATH):
        assert adr_path.is_file(), f"missing {adr_path}"
        paths = _anchor_paths(adr_path.read_text(encoding="utf-8"))
        assert paths, f"{adr_path.name}: Anchors table must list at least one path"
        for raw_path in paths:
            path_only = raw_path.split("#")[0]
            candidate = _REPO_ROOT / path_only
            assert candidate.exists(), (
                f"{adr_path.name}: Anchors table references {raw_path!r} "
                f"which does not exist on disk"
            )
