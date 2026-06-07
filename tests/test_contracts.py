"""C3 — Wire / schema contracts with external systems.

The boundaries where PromptPotter meets something it does not control: the LLM
SDK (error translation + retry + rate-limit gating), the backend ``GET /pipeline``
parse, and Pydantic ``extra='forbid'`` on operator/optimizer config. The math
must be wrong-reveal at the seam — a malformed wire payload or an un-rejected
stray field is a contract break, not a smoke test.

Sections:
  1. ``OpenAICompatibleClient`` — HTTP error translation + single repair retry.
  2. ``RateLimiter`` — rolling-window RPM/TPM gating + reservation correction.
  3. Pipeline config — ``extra='forbid'`` rejection + override validation.
  4. Pipeline schema parse — backend ``GET /pipeline`` → ``PipelineSchema``.
  5. Dataset ``pipeline.json`` constraints.
  6. M12 control-remote wire-contract drift (OpenAPI/AsyncAPI ↔ records).
  7. Origin-resolution gate — parser split + readiness checklist.
  8. Read-only webapp API — typed envelopes + connector wire adapters.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from _helpers import MockHTTPError, make_http_error
from pydantic import ValidationError

from promptpotter.application.config import CampaignConfig
from promptpotter.application.optimization.validators.l1_strict import (
    build_l1_output_schema,
    validate_overrides,
)
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema
from promptpotter.infrastructure.llm import OpenAICompatibleClient, RateLimiter
from promptpotter.shared.errors import RequestTooLargeError

REPO = Path(__file__).resolve().parent.parent
DATASETS_DIR = REPO / "datasets"

# ===========================================================================
# 1. OpenAICompatibleClient — error translation + repair retry
# ===========================================================================


class _RawResponse:
    def __init__(self, parsed, headers=None):
        self._parsed = parsed
        self.headers = headers or {}

    def parse(self):
        return self._parsed


@pytest.fixture
def llm_client():
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="http://localhost:1234",
        provider_name="test",
    )
    mock_async_client = AsyncMock()
    client._client = mock_async_client
    return client, mock_async_client


async def test_404_translates_to_model_not_found(llm_client):
    client, mock_async = llm_client
    mock_async.chat.completions.with_raw_response.create = AsyncMock(
        side_effect=make_http_error(404, "model not found")
    )
    with pytest.raises(ValueError, match="not found on test"):
        await client.chat(messages=[{"role": "user", "content": "x"}], model="test-model")


@pytest.mark.parametrize("status", [413, 429])
async def test_request_too_large_terminal(llm_client, status):
    client, mock_async = llm_client
    body = f"Limit 8000, Requested 9660 (status {status})"
    mock_async.chat.completions.with_raw_response.create = AsyncMock(
        side_effect=make_http_error(status, body)
    )
    with pytest.raises(RequestTooLargeError) as excinfo:
        await client.chat(messages=[{"role": "user", "content": "x"}], model="test-model")
    assert excinfo.value.limit == 8000
    assert excinfo.value.requested == 9660


async def test_json_validate_failed_salvage(llm_client):
    client, mock_async = llm_client
    body = (
        "400 Bad Request {'error': {'code': 'json_validate_failed', "
        "'failed_generation': '{\"ok\": 1}'}}"
    )
    mock_async.chat.completions.with_raw_response.create = AsyncMock(
        side_effect=make_http_error(400, body)
    )
    resp = await client.chat(messages=[{"role": "user", "content": "x"}], model="test-model")
    assert resp.parsed == {"ok": 1}


async def test_unknown_error_reraised_no_app_retry(llm_client):
    client, mock_async = llm_client
    call_count = [0]

    async def mock_create(**_kwargs):
        call_count[0] += 1
        raise make_http_error(500, "server error")

    mock_async.chat.completions.with_raw_response.create = mock_create
    with pytest.raises(MockHTTPError, match="server error"):
        await client.chat(messages=[{"role": "user", "content": "x"}], model="test-model")
    assert call_count[0] == 1, "app layer must not retry — SDK handles 5xx internally"


async def test_pydantic_validation_failure_retries_once_then_raises(llm_client):
    """When a response fails Pydantic validation against ``response_model``,
    ``chat()`` retries exactly once with a repair-hint user turn appended. Two
    malformed replies raise :class:`MetaPromptParseError` with ``attempts=2`` —
    the L1 catch site converts that into a ``ValidationFailure`` wound.
    """
    from pydantic import BaseModel

    from promptpotter.infrastructure.llm.json_parse import MetaPromptParseError

    class _Strict(BaseModel):
        required_field: str

    client, mock_async = llm_client
    bad = type(
        "Bad",
        (),
        {
            "choices": [
                type(
                    "C",
                    (),
                    {
                        "message": type("M", (), {"content": '{"wrong_field": "x"}'})(),
                        "finish_reason": "stop",
                    },
                )()
            ],
            "usage": None,
            "model": "test-model",
        },
    )()
    mock_async.chat.completions.with_raw_response.create = AsyncMock(return_value=_RawResponse(bad))
    with pytest.raises(MetaPromptParseError) as excinfo:
        await client.chat(
            messages=[{"role": "user", "content": "x"}],
            model="test-model",
            response_model=_Strict,
        )
    assert excinfo.value.attempts == 2
    assert mock_async.chat.completions.with_raw_response.create.await_count == 2
    second_call_messages = mock_async.chat.completions.with_raw_response.create.await_args_list[
        1
    ].kwargs["messages"]
    assert second_call_messages[0] == {"role": "user", "content": "x"}
    assert second_call_messages[-2]["role"] == "assistant"
    assert "schema validation" in second_call_messages[-1]["content"]


# ===========================================================================
# 2. RateLimiter — rolling-window RPM + TPM
# ===========================================================================


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr("promptpotter.infrastructure.llm.rate_limit.time.monotonic", c)
    return c


@pytest.fixture
def fast_sleep(monkeypatch, clock):
    async def _sleep(seconds):
        clock.advance(seconds)

    monkeypatch.setattr("promptpotter.infrastructure.llm.rate_limit.asyncio.sleep", _sleep)


async def test_rpm_cap_blocks_until_window_rolls(clock, fast_sleep):
    rl = RateLimiter(rpm=3, window_s=60.0)
    for _ in range(3):
        await rl.acquire(100)
    assert clock.now == 0.0
    await rl.acquire(100)  # 4th must wait for the oldest reservation to age out
    assert clock.now == pytest.approx(60.0)


async def test_tpm_cap_blocks_until_tokens_age_out(clock, fast_sleep):
    rl = RateLimiter(tpm=8000, window_s=60.0)
    await rl.acquire(3000)
    await rl.acquire(3000)
    assert clock.now == 0.0
    await rl.acquire(3000)  # 9000 > 8000, wait for oldest 3000 to expire
    assert clock.now == pytest.approx(60.0)


async def test_record_actual_corrects_reservation(clock, fast_sleep):
    rl = RateLimiter(tpm=8000, window_s=60.0)
    await rl.acquire(5000)
    rl.record_actual(5000, 1000)  # actual usage was much smaller
    await rl.acquire(6000)  # 1000 + 6000 ≤ 8000 → no block
    assert clock.now == 0.0


# ===========================================================================
# 3. CampaignConfig — extra='forbid' rejection + explicit-knob requirement
# ===========================================================================


@pytest.mark.parametrize(
    "payload",
    [
        {"dataset_name": "x", "nonsense_key": 1},  # unknown top-level key
        {"optimization": {"zero_signal_filtre_enabled": True}},  # typo'd optimization key
        {"dataset_name": "x", "pipeline_params": {"steps": ["a"]}},  # runtime field, not config
        {"optimization": {"l1_patience": 3}},  # omits required improvement/degradation thresholds
    ],
    ids=[
        "unknown_top_level",
        "unknown_optimization",
        "runtime_pipeline_params",
        "missing_required",
    ],
)
def test_campaign_config_rejects_unknown_or_incomplete(payload):
    """``extra='forbid'`` at every nesting level + no-default per-dataset knobs."""
    with pytest.raises(ValidationError):
        CampaignConfig.model_validate(payload)


def test_all_persisted_campaign_jsons_parse() -> None:
    """Every ``datasets/*/campaign.json`` must load into the model unchanged."""
    from promptpotter.application.config import load_campaign_config

    files = sorted(DATASETS_DIR.glob("*/campaign.json"))
    assert files, "no campaign.json fixtures found — test setup broken"
    for path in files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        payload = raw.get("campaign_config", raw)
        assert isinstance(load_campaign_config(payload), CampaignConfig), f"{path} failed"


# ===========================================================================
# 4. Pipeline parse — backend GET /pipeline → PipelineSchema + override validation
# ===========================================================================


def _bbeh_schema() -> PipelineSchema:
    return PipelineSchema(
        name="bbeh",
        available_models=["openai/gpt-oss-120b"],
        nodes=[
            PipelineNode(
                name="llm_only",
                param_keys={"model", "reasoning_effort", "response_format", "temperature"},
                param_allowed_values={
                    "reasoning_effort": ["none", "default", "low", "medium", "high"],
                    "response_format": ["text", "json"],
                },
                param_types={"temperature": "number", "model": "string"},
            ),
        ],
    )


def test_parse_pipeline_response():
    """Self-describing TermNorm pipeline → PipelineSchema with ordered, typed nodes."""
    data = {
        "config": {
            "name": "TermNorm",
            "version": "2.0",
            "nodes": {
                "cache_lookup": {
                    "type": "cache",
                    "short_circuit": True,
                    "node_role": "cache",
                    "config": {},
                    "optimizer": {"langfuse_type": "span"},
                },
                "web_search": {
                    "type": "tool",
                    "node_role": "enricher",
                    "config": {"max_sites": 7},
                    "optimizer": {
                        "param_keys": ["max_sites"],
                        "observation_name": "web_search",
                        "observation_mappings": [{"pipeline_key": "web_sources"}],
                        "langfuse_type": "tool",
                    },
                },
            },
            "pipelines": {"default": ["cache_lookup", "web_search"]},
        },
    }
    schema = parse_pipeline_response(data)
    assert schema.name == "termnorm"
    assert schema.version == "2.0"
    assert [s.name for s in schema.nodes] == ["cache_lookup", "web_search"]
    assert schema.nodes[0].short_circuit is True
    assert schema.nodes[0].node_type == "cache"
    assert schema.nodes[1].param_keys == {"max_sites"}
    assert schema.nodes[1].observation_mappings[0].pipeline_key == "web_sources"


def test_parse_pipeline_response_threads_param_allowed_values():
    data = {
        "config": {
            "name": "bbeh",
            "available_models": ["openai/gpt-oss-120b"],
            "nodes": {
                "llm_only": {
                    "type": "generation",
                    "config": {"reasoning_effort": "medium", "threshold": 70},
                    "optimizer": {
                        "param_keys": [
                            "reasoning_effort",
                            "response_format",
                            "temperature",
                            "max_tokens",
                            "threshold",
                        ],
                        "param_allowed_values": {
                            "reasoning_effort": ["none", "low", "medium", "high"],
                            "response_format": ["text", "json"],
                        },
                    },
                }
            },
            "pipelines": {"default": ["llm_only"]},
        }
    }
    node = parse_pipeline_response(data).get_node("llm_only")
    assert node is not None
    assert node.param_allowed_values["reasoning_effort"] == ["none", "low", "medium", "high"]
    assert node.param_allowed_values["response_format"] == ["text", "json"]
    # WELL_KNOWN_PARAM_TYPES resolves universal LLM params without declaration.
    assert node.param_types["temperature"] == "number"
    assert node.param_types["max_tokens"] == "integer"
    assert node.param_types["reasoning_effort"] == "string"
    # Backend-specific params fall back to inference from the config default's type.
    assert node.param_types["threshold"] == "integer"


def test_node_config_schema_full_surface_excludes_prompt_fields():
    """Operator-steer config surface: every ``param_keys`` tunable EXCEPT prompt
    fields, with widget kinds + options. ``model`` is INCLUDED but flagged
    ``optimizer_locked`` under a strict campaign (shown for operator, locked for
    the optimizer); prompt fields are owned by the prompt editor and excluded."""
    data = {
        "config": {
            "name": "gsm8k",
            "available_models": ["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
            "nodes": {
                "llm_only": {
                    "type": "generation",
                    "config": {
                        "model": "openai/gpt-oss-120b",
                        "temperature": 0.0,
                        "reasoning_effort": "medium",
                    },
                    "optimizer": {
                        "param_keys": [
                            "temperature",
                            "max_tokens",
                            "model",
                            "reasoning_effort",
                            "persona",
                            "instruction",
                            "answer_format",
                        ],
                        "param_allowed_values": {"reasoning_effort": ["low", "medium", "high"]},
                    },
                }
            },
            "pipelines": {"default": ["llm_only"]},
        }
    }
    schema = parse_pipeline_response(data)
    params = {p.key: p for p in schema.node_config_schema(forbidden_strict=True)["llm_only"]}
    assert set(params) == {"temperature", "max_tokens", "model", "reasoning_effort"}
    assert params["model"].kind == "model"
    assert params["model"].options == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    assert params["model"].optimizer_locked is True
    assert params["reasoning_effort"].kind == "enum"
    assert params["reasoning_effort"].options == ["low", "medium", "high"]
    assert params["temperature"].kind == "number"
    assert params["max_tokens"].kind == "number"  # WELL_KNOWN integer → number widget
    assert params["max_tokens"].value is None
    relaxed = schema.node_config_schema(forbidden_strict=False)["llm_only"]
    assert {p.key for p in relaxed if p.optimizer_locked} == set()


@pytest.mark.parametrize(
    ("override", "strict_kw", "axis", "reason", "allowed_contains"),
    [
        (
            {"reasoning_effort": "minimal"},
            {},
            "llm_only.reasoning_effort",
            "not_in_param_allowed_values",
            "medium",
        ),
        ({"temperature": "0.2"}, {}, "llm_only.temperature", "type_mismatch", "number"),
        ({"model": "gpt-5-turbo-xxl"}, {}, "llm_only.model", "forbidden_axis", None),
        (
            {"model": "gpt-5-turbo-xxl"},
            {"forbidden_axes_strict": False},
            "llm_only.model",
            "not_in_available_models",
            None,
        ),
    ],
    ids=["enum", "stringified_number", "model_strict", "model_lax"],
)
def test_validate_overrides_rejects(override, strict_kw, axis, reason, allowed_contains):
    """One failure per poisonous axis — caught before the wire payload is built."""
    failures = validate_overrides({"llm_only": override}, _bbeh_schema(), **strict_kw)
    assert len(failures) == 1
    assert failures[0].axis == axis
    assert failures[0].reason == reason
    if allowed_contains is not None:
        assert allowed_contains in failures[0].allowed


def test_build_l1_output_schema_emits_enum_for_constrained_params():
    """Three override slots, each constrained server-side: pipeline_params_override
    (per-node enum/type grafting), prompt_fields_override (six string keys),
    task_context_override (two string keys). Splitting the slots is what makes the
    un-nesting repair unnecessary — additionalProperties:false rejects a prompt
    field nested under ``pipeline_params_override.llm_only``."""
    out = build_l1_output_schema(_bbeh_schema())
    variant_props = out["schema"]["properties"]["variants"]["items"]["properties"]

    pp_props = variant_props["pipeline_params_override"]["properties"]
    llm_props = pp_props["llm_only"]["properties"]
    assert llm_props["reasoning_effort"] == {
        "type": "string",
        "enum": ["none", "default", "low", "medium", "high"],
    }
    assert llm_props["response_format"] == {"type": "string", "enum": ["text", "json"]}
    assert llm_props["temperature"] == {"type": "number"}
    assert "enum" not in llm_props["temperature"]
    assert variant_props["pipeline_params_override"]["additionalProperties"] is False
    assert pp_props["llm_only"]["additionalProperties"] is False

    pf_slot = variant_props["prompt_fields_override"]
    assert pf_slot["additionalProperties"] is False
    pf_props = pf_slot["properties"]
    for field in (
        "persona",
        "task_intent",
        "problem_description",
        "instruction",
        "thinking_style",
        "answer_format",
    ):
        assert pf_props[field] == {"type": "string"}
    assert "instruction" not in llm_props
    assert "answer_format" not in llm_props

    tc_slot = variant_props["task_context_override"]
    assert tc_slot["additionalProperties"] is False
    tc_props = tc_slot["properties"]
    assert tc_props["upstream_context"] == {"type": "string"}
    assert tc_props["downstream_context"] == {"type": "string"}

    assert variant_props["variant_name"] == {"type": "string"}
    assert out["strict"] is False


# ===========================================================================
# 5. Dataset pipeline.json file constraints
# ===========================================================================


def test_no_numeric_max_tokens_in_dataset_pipeline_configs() -> None:
    """Numeric ``max_tokens`` defaults re-introduce the BBEH reasoning-budget trap.
    Operators raise caps per-cycle via campaign.json — never in the dataset file."""
    pipeline_files = sorted(
        p for p in DATASETS_DIR.glob("*/pipeline.json") if p.parent.name != "_optimizer"
    )
    assert pipeline_files, "no datasets/*/pipeline.json found — wrong cwd?"
    offenders: list[str] = []
    for path in pipeline_files:
        spec = json.loads(path.read_text(encoding="utf-8"))
        for node_name, node in (spec.get("nodes") or {}).items():
            mt = (node.get("config") or {}).get("max_tokens", "absent")
            if isinstance(mt, int):
                offenders.append(f"{path.relative_to(REPO)} :: {node_name} = {mt}")
    assert not offenders, (
        "Numeric max_tokens default(s) in dataset pipeline.json:\n  "
        + "\n  ".join(offenders)
        + "\nUse `null` or omit; operators override per-cycle via campaign.json."
    )


def test_optimizer_and_backend_pipelines_share_parser_and_shape() -> None:
    """Same parser, same top-level shape, same per-node optimizer sub-object."""
    optimizer_pipeline = DATASETS_DIR / "_optimizer/pipeline.json"
    backend_pipelines = sorted(
        p for p in DATASETS_DIR.glob("*/pipeline.json") if p.parent.name != "_optimizer"
    )
    assert optimizer_pipeline.exists(), optimizer_pipeline
    assert backend_pipelines, "no backend pipeline.json files under datasets/"

    for path in [optimizer_pipeline, *backend_pipelines]:
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = {"name", "nodes", "pipelines"} - set(data.keys())
        assert not missing, f"{path}: missing required top-level fields: {sorted(missing)}"
        assert "default" in data["pipelines"], f"{path}: pipelines.default is required"

        schema = parse_pipeline_response(data)
        assert schema.name, f"{path}: parsed schema has empty name"
        assert schema.nodes, f"{path}: parsed schema has zero nodes"

        active_in_manifest = [n for n in data["pipelines"]["default"] if n in data["nodes"]]
        assert [n.name for n in schema.nodes] == active_in_manifest, (
            f"{path}: parser walked a different node order than pipelines.default"
        )
        for node in schema.nodes:
            assert node.name, f"{path}: node has empty name"
            assert node.wire_type, f"{path}::{node.name} has empty wire_type"
            assert node.langfuse_type, f"{path}::{node.name} has empty langfuse_type"


# ===========================================================================
# 6. M12 Control-remote wire-contract drift (OpenAPI/AsyncAPI YAML ↔ Python)
# ===========================================================================
# The YAML wire-contract files are the source of truth for what the system can
# ingress/egress; these assert the YAML and the Python code never drift apart.

import re  # noqa: E402
from typing import Any, Literal, get_args, get_type_hints  # noqa: E402

import yaml  # noqa: E402

from promptpotter.domain import run_records  # noqa: E402

_OPENAPI_PATH = REPO / "docs" / "specs" / "m12-api-openapi.yaml"
_ASYNCAPI_PATH = REPO / "docs" / "specs" / "m12-events-asyncapi.yaml"
_CONTRACT_PATH = REPO / "docs" / "adr" / "0001-m12-control-plane.md"
_IDENTITY_ADR_PATH = REPO / "docs" / "adr" / "0002-identity-foundation.md"
_SPEND_ADR_PATH = REPO / "docs" / "adr" / "0003-spend-and-tenancy.md"
_ADMIN_ADR_PATH = REPO / "docs" / "adr" / "0004-operator-admin-channels.md"

# Projection-only event kinds — emitted on the SSE channel but have no
# underlying record class in `domain/run_records.py`. Synthesized by
# EventStreamView at Profile A; expand only at profile boundaries.
#   - `stream_snapshot`: the leading snapshot frame (security box 14).
# `command` / `command_ack` are NOT projection-only — they ride records
# in the union (`CommandRecord` / `CommandAckRecord`) and round-trip
# through the canonical ledger.
_PROJECTION_ONLY_KINDS: frozenset[str] = frozenset({"stream_snapshot"})

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


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


def _check_anchor_paths(adr_name: str, paths: list[str]) -> None:
    """Anchors cite stable contracts (specs + the code/domain the ADR depends on),
    never test files — tests are enforcement detail that must move freely. Each
    cited path must exist; a ``tests/`` path is itself a contract violation, so a
    test refactor can never redden a doc gate."""
    assert paths, f"{adr_name}: Anchors table must list at least one path"
    for raw_path in paths:
        assert not raw_path.startswith("tests/"), (
            f"{adr_name}: Anchors table cites a test file ({raw_path!r}). Anchors "
            "name stable contracts; reference the enforcing test in prose, not the "
            "anchor table — tests move freely and must not redden a doc gate."
        )
        # Strip section-anchor qualifiers like 'docs/architecture.md#section'.
        candidate = REPO / raw_path.split("#")[0]
        assert candidate.exists(), (
            f"{adr_name}: Anchors table references {raw_path!r} which does not exist on disk"
        )


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
    _check_anchor_paths(
        "0001-m12-control-plane.md", _anchor_paths(_CONTRACT_PATH.read_text(encoding="utf-8"))
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
        _check_anchor_paths(adr_path.name, _anchor_paths(adr_path.read_text(encoding="utf-8")))


# ===========================================================================
# 7. Origin-resolution gate — parser split + deterministic readiness checklist
# ===========================================================================
# The header-agnostic parser materializes arbitrary column names once a mapping
# is confirmed; the closed-set checklist blocks mint until every origin field is
# CONFIRMED (the column mapping AND the once-hidden config defaults), with
# `task_description` the one field that lands UNSET (no default framing).

import io  # noqa: E402

import openpyxl  # noqa: E402

from promptpotter.application.datasets.csv_ingest import (  # noqa: E402
    IngestError,
    closed_label_set,
    materialize_samples,
    read_tabular,
)
from promptpotter.application.datasets.draft_campaign import (  # noqa: E402
    DraftCampaignRegistry,
)
from promptpotter.application.datasets.origin_readiness import (  # noqa: E402
    origin_readiness,
    resolution_block,
)
from promptpotter.config.settings import settings  # noqa: E402
from promptpotter.domain.identity import TenantId  # noqa: E402
from promptpotter.domain.origin_provenance import Provenance  # noqa: E402


def _xlsx_blob(rows: list[list[str]]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_read_tabular_is_header_agnostic_across_formats(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every supported format parses to the same header-agnostic table (the parser
    never requires literal `query`/`ground_truth` — that's the resolver's job), a
    confirmed mapping materializes Samples, and hardened mode bans Excel."""
    expected = {"input": ("q1", "q2"), "gt": ("a1", "a2")}
    xlsx = _xlsx_blob([["input", "gt"], ["q1", "a1"], ["q2", "a2"]])

    def assert_shape(table: object) -> None:
        t = table
        assert isinstance(t, type(read_tabular(b"input,gt\nq1,a1\n")))
        assert set(t.headers) == {"input", "gt"}
        assert len(t.rows) == 2
        for col, vals in expected.items():
            assert tuple(r[col] for r in t.rows) == vals

    csv = read_tabular(b"input,gt\nq1,a1\nq2,a2\n", fmt="csv")
    assert_shape(csv)
    assert_shape(read_tabular(b"input\tgt\nq1\ta1\nq2\ta2\n", fmt="tsv"))
    assert_shape(read_tabular(b'[{"input":"q1","gt":"a1"},{"input":"q2","gt":"a2"}]', fmt="json"))
    # JSON object-of-columns (HuggingFace-style) transposes to the same rows.
    assert_shape(read_tabular(b'{"input":["q1","q2"],"gt":["a1","a2"]}', fmt="json"))
    assert_shape(read_tabular(b'{"input":"q1","gt":"a1"}\n{"input":"q2","gt":"a2"}\n', fmt="jsonl"))
    assert_shape(read_tabular(xlsx, fmt="xlsx"))

    samples = materialize_samples(csv, query_col="input", ground_truth_col="gt")
    assert [(s.id, s.query, s.ground_truth) for s in samples] == [(0, "q1", "a1"), (1, "q2", "a2")]

    # Hardened mode bans Excel (macro/zip-bomb/XXE vector); text formats stay readable.
    monkeypatch.setattr(settings, "HARDENED_MODE", True)
    with pytest.raises(IngestError) as exc:
        read_tabular(xlsx, fmt="xlsx")
    assert exc.value.reason == "hardened_blocked"
    assert read_tabular(b"input,gt\nq1,a1\n", fmt="csv").headers == ("input", "gt")


def test_readiness_gates_on_closed_set() -> None:
    """Only columns + task framing + answer space gate; config is not gated."""
    registry = DraftCampaignRegistry()
    tenant = TenantId("t1")

    # Non-literal headers → columns UNSET; task_description has no default → UNSET.
    # Config (connector/scoring/max_rounds/provider/model/node_config) is NOT
    # gated — it carries a sane default the operator edits, so it is never a gap.
    draft = registry.create(
        tenant_id=tenant,
        slug="d1",
        n_samples=1,
        sample_preview=[{"input": "q", "gt": "a"}],
        headers=["input", "gt"],
    )
    verdict = origin_readiness(draft)
    assert not verdict.complete
    assert {g.field for g in verdict.gaps} == {
        "column.query",
        "column.ground_truth",
        "task_description",
    }
    # Config carries no provenance entry — it's not gated.
    assert "connector" not in draft.field_provenance

    # Confirm the columns (operator-stated) + state the framing → every
    # gated field CONFIRMED.
    opened = draft.confirm_columns(query_col="input", ground_truth_col="gt").apply_resolution(
        values={"raw_task_description": "Map lab-test names to codes."},
        provenance={"task_description": Provenance.CONFIRMED},
    )
    assert origin_readiness(opened).complete
    assert opened.field_provenance["column.query"] is Provenance.CONFIRMED
    # The on-disk block carries provenance per gated field.
    block = resolution_block(opened)
    assert block["provenance"]["task_description"] == "confirmed"

    # Literal headers auto-confirm the columns, but task_description still gates.
    literal = registry.create(
        tenant_id=tenant,
        slug="d2",
        n_samples=1,
        sample_preview=[{"query": "q", "ground_truth": "a"}],
        headers=["query", "ground_truth"],
    )
    assert not origin_readiness(literal).complete
    assert {g.field for g in origin_readiness(literal).gaps} == {"task_description"}

    # Closed-label target → the gate also requires every label be enumerated in
    # the prompt, so the optimizer can't be handed a partial taxonomy (the bug
    # that left non-`financial` rows unscoreable). `closed_label_set` is the
    # open-vs-closed detector: a 4-way gold column closes, a 1:1 free-text
    # column stays open.
    assert closed_label_set(["a", "b", "a", "c", "d"], n_rows=8) == ("a", "b", "c", "d")
    assert closed_label_set([str(i) for i in range(20)], n_rows=20) is None  # distinct ≈ n
    assert closed_label_set(["only"], n_rows=4) is None  # single value isn't a choice

    labels = ("actionable", "financial", "informational", "other")
    classed = registry.create(
        tenant_id=tenant,
        slug="d5",
        n_samples=8,
        sample_preview=[{"query": "q", "ground_truth": "other"}],
        headers=["query", "ground_truth"],
        column_label_sets={"ground_truth": labels},
    ).apply_resolution(
        values={"raw_task_description": "Sort the email. Pick another label."},
        provenance={"task_description": Provenance.CONFIRMED},
    )
    # task_description names none of the labels (and "another" must NOT count as
    # "other") → the answer_space gap is open, listing every missing label.
    gap = next(g for g in origin_readiness(classed).gaps if g.field == "answer_space")
    assert all(lab in gap.hint for lab in labels)
    # A prompt that enumerates all four closes it (the proposer's job, gated here).
    enumerated = classed.apply_resolution(
        values={
            "origin_prompt_fields": {
                "answer_format": "Reply with exactly one of: "
                "actionable, financial, informational, other."
            }
        }
    )
    assert origin_readiness(enumerated).complete


def test_low_confidence_proposal_blocks_until_confirmed() -> None:
    """A PROPOSED field (low-confidence resolver finding) blocks mint; CONFIRMED opens it."""
    registry = DraftCampaignRegistry()
    draft = registry.create(
        tenant_id=TenantId("t1"),
        slug="d3",
        n_samples=1,
        sample_preview=[{"query": "q", "ground_truth": "a"}],
        headers=["query", "ground_truth"],
    )
    # Resolver proposes a framing at low confidence → PROPOSED, still blocks.
    proposed = draft.apply_resolution(
        values={"raw_task_description": "maybe map codes"},
        provenance={"task_description": Provenance.PROPOSED},
    )
    verdict = origin_readiness(proposed)
    assert not verdict.complete
    assert verdict.gaps[0].reason == "proposed_unconfirmed"

    # Operator confirms → complete.
    confirmed = proposed.apply_resolution(provenance={"task_description": Provenance.CONFIRMED})
    assert origin_readiness(confirmed).complete


def test_optimizer_locks_surface_connector_clamp() -> None:
    """The draft wire exposes TermNorm's reasoning clamp + forbidden axes pre-commit.

    A draft's `pipeline_overlay` is empty until commit, so the new-campaign UI
    can only show "the optimizer is locked out of medium/high thinking" if the
    wire carries the connector's seed. `draft_wire_with_locks` is that surface.
    """
    from promptpotter.application.jobs.launcher import draft_wire_with_locks

    registry = DraftCampaignRegistry()
    draft = registry.create(
        tenant_id=TenantId("t1"),
        slug="d4",
        n_samples=1,
        sample_preview=[{"query": "q", "ground_truth": "a"}],
        headers=["query", "ground_truth"],
    )
    locks = draft_wire_with_locks(draft)["optimizer_locks"]

    assert locks["pipeline"] == ["llm_only"]
    # Model/provider are pinned campaign-wide by forbidden_axes_strict (default on).
    assert locks["forbidden_axes"] == ["model", "provider"]
    node = locks["nodes"]["llm_only"]
    assert node["config"]["reasoning_effort"] == "low"
    # `medium` / `high` are absent from the allowed set → crossed out in the UI.
    allowed = node["param_allowed_values"]["reasoning_effort"]
    assert "low" in allowed and "medium" not in allowed and "high" not in allowed


# ===========================================================================
# 8. Read-only webapp API — typed envelopes + connector wire adapters
# ===========================================================================
# Each per-cycle live-read endpoint round-trips a typed envelope from one seeded
# campaign+cycle, so the ledger record schema has an external consumer that fails
# loudly on drift. The fixture rides the foundation `seed_campaign_cycle`; only
# the four API-contract-specific ledger records are laid here.

from collections.abc import Iterator  # noqa: E402

from _factories import seed_campaign_cycle  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from promptpotter.connectors.termnorm import (  # noqa: E402
    TermNormSession,
    termnorm_wire_adapter,
)
from promptpotter.domain.cycle_paths import CycleDir  # noqa: E402
from promptpotter.domain.run_records import (  # noqa: E402
    PhaseRecord,
    ResumeCheckpointKind,
    ResumeCheckpointRecord,
)
from promptpotter.infrastructure.backend import BackendClient  # noqa: E402
from promptpotter.infrastructure.ledger import CycleEventLog  # noqa: E402
from promptpotter.infrastructure.store import Stores, cycle_dir_for  # noqa: E402
from promptpotter.main import app  # noqa: E402
from promptpotter.presentation.api.deps import build_stores_from_identity  # noqa: E402

_API_CAMPAIGN_ID = "apitest__20260101-000000"
_API_CYCLE_ID = "cycle_apitest_001"


@pytest.fixture
def seeded_tenant(built_stores: Stores) -> Iterator[tuple[TestClient, str, str]]:
    """One campaign + cycle on disk (round-3 dashboard + a four-record ledger:
    two phases bracketing a ROUND_WINNER, then a FORK_CUT), wired through the
    per-tenant store override. Disk seed rides the foundation factory; only the
    API-contract-specific ledger records are laid here."""
    handle = seed_campaign_cycle(
        built_stores.base_dir,
        campaign_id=_API_CAMPAIGN_ID,
        cycle_id=_API_CYCLE_ID,
        index={"parent_session_id": "s_abc"},
        dashboard={"round": 3, "best": 0.812},
    )
    ledger = CycleEventLog.open(CycleDir(handle.cycle_dir))
    ledger.append(PhaseRecord(phase="round", event="enter", round=0))
    ledger.append(
        ResumeCheckpointRecord(
            kind=ResumeCheckpointKind.ROUND_WINNER,
            inputs_ref={"candidate_ids": ["c1"], "round_num": 0},
            outcome="c1",
            round=0,
        )
    )
    ledger.append(PhaseRecord(phase="round", event="complete", round=0, payload={"acc": 0.6}))
    ledger.append(
        ResumeCheckpointRecord(
            kind=ResumeCheckpointKind.FORK_CUT,
            inputs_ref={"from_round": 1},
            outcome="cycle_apitest_001_fork_abc",
            data={"forked_at": "2026-04-30T12:00:00+00:00"},
        )
    )

    def _override_stores() -> Stores:
        return built_stores

    app.dependency_overrides[build_stores_from_identity] = _override_stores
    try:
        yield TestClient(app), handle.campaign_id, handle.cycle_id
    finally:
        app.dependency_overrides.clear()


def test_campaigns_list_returns_manifest(seeded_tenant: tuple[TestClient, str, str]) -> None:
    client, campaign_id, _ = seeded_tenant
    resp = client.get("/api/v1/campaigns")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["campaigns"][0]["campaign_id"] == campaign_id
    assert body["campaigns"][0]["dataset_name"] == "apitest"


def test_log_md_returns_markdown_envelope(seeded_tenant: tuple[TestClient, str, str]) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/log_md")
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope_id"] == cycle_id
    assert "# Campaign log" in body["markdown"]


def test_ledger_returns_typed_records(seeded_tenant: tuple[TestClient, str, str]) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/ledger")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cycle_id"] == cycle_id
    assert body["next_offset"] == 4
    types = [r["record_type"] for r in body["records"]]
    assert types == ["phase", "decision", "phase", "decision"]


def test_ledger_since_offset_skips_earlier_records(
    seeded_tenant: tuple[TestClient, str, str],
) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/ledger?since=2")
    body = resp.json()
    assert [r["offset"] for r in body["records"]] == [2, 3]
    assert body["next_offset"] == 4


def test_decisions_filters_to_decision_records(
    seeded_tenant: tuple[TestClient, str, str],
) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/decisions")
    assert resp.status_code == 200
    body = resp.json()
    kinds = [d["kind"] for d in body["decisions"]]
    assert kinds == ["round_winner", "fork_cut"]


def test_forks_derives_from_fork_cut_records(seeded_tenant: tuple[TestClient, str, str]) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/forks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["parent_cycle_id"] == cycle_id
    assert len(body["forks"]) == 1
    fork = body["forks"][0]
    assert fork["fork_cycle_id"] == "cycle_apitest_001_fork_abc"
    assert fork["from_round"] == 1
    assert fork["forked_at"] == "2026-04-30T12:00:00+00:00"


def test_dashboard_is_per_cycle_not_session_root(
    seeded_tenant: tuple[TestClient, str, str],
) -> None:
    """Each cycle serves its OWN dashboard.json — a fork does not collapse to
    the session root's file (state-sync Phase 2)."""
    client, campaign_id, cycle_id = seeded_tenant

    # Root cycle's dashboard (seeded at round 3). A fork carries its own.
    stores = app.dependency_overrides[build_stores_from_identity]()
    fork_id = f"{cycle_id}_fork_abc123"
    fork_dir = cycle_dir_for(stores.base_dir, campaign_id, fork_id)
    fork_dir.mkdir(parents=True, exist_ok=True)
    (fork_dir / "dashboard.json").write_text(
        json.dumps({"phase": "l1_score", "round": 7, "best": 0.91}), encoding="utf-8"
    )

    root_resp = client.get(f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/dashboard")
    fork_resp = client.get(f"/api/v1/campaigns/{campaign_id}/cycles/{fork_id}/dashboard")
    assert root_resp.json()["round"] == 3
    assert fork_resp.json()["round"] == 7  # the fork's own file, not the root's


def test_active_returns_404_when_pointer_missing(
    seeded_tenant: tuple[TestClient, str, str],
) -> None:
    """No pointer file under the tenant's workspace dir → 404."""
    client, _, _ = seeded_tenant
    resp = client.get("/api/v1/sessions/active")
    assert resp.status_code == 404


def test_active_returns_pointer_when_present(
    seeded_tenant: tuple[TestClient, str, str],
) -> None:
    """Pointer written through the per-tenant API surfaces on /sessions/active,
    and /sessions/active/live-state reflects pause + spend-cap runtime flags."""
    from promptpotter.infrastructure.store import save_active_pointer

    client, campaign_id, cycle_id = seeded_tenant
    stores = app.dependency_overrides[build_stores_from_identity]()
    save_active_pointer(
        stores.tenant_id,
        "s_abc",
        campaign_id,
        cycle_id,
        projects_root=stores.projects_root,
    )
    resp = client.get("/api/v1/sessions/active")
    assert resp.status_code == 200
    assert resp.json() == {
        "tenant_id": "default",
        "session_id": "s_abc",
        "campaign_id": campaign_id,
        "cycle_id": cycle_id,
    }

    # /sessions/active/live-state is the stable façade: active pointer → live state.
    live = client.get("/api/v1/sessions/active/live-state")
    assert live.status_code == 200
    body = live.json()
    assert body["round"] == 3
    # Run-control facts the dashboard projection doesn't carry default to
    # not-paused / uncapped when the runtime flags are absent.
    assert body["is_paused"] is False
    assert body["current_spend_cap_usd"] is None

    # With a pause.flag + spend_cap.json on the active cycle, /live-state reflects
    # both — run controls read pause-state from here, not telemetry freshness
    # (a paused runner emits no events).
    runtime = cycle_dir_for(stores.base_dir, campaign_id, cycle_id) / ".runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "pause.flag").write_text("requested_at=now\n", encoding="utf-8")
    (runtime / "spend_cap.json").write_text('{"max_usd": 4.5}', encoding="utf-8")
    live2 = client.get("/api/v1/sessions/active/live-state").json()
    assert live2["is_paused"] is True
    assert live2["current_spend_cap_usd"] == 4.5


def test_files_lists_cycle_and_campaign_artifacts(
    seeded_tenant: tuple[TestClient, str, str],
) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/files")
    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign_id"] == campaign_id
    assert body["cycle_id"] == cycle_id
    cycle_paths = {e["path"] for e in body["entries"] if e["scope"] == "cycle"}
    campaign_paths = {e["path"] for e in body["entries"] if e["scope"] == "campaign"}
    # dashboard.json is per-cycle — it sits in this cycle's own dir, not the campaign dir.
    assert {"index.json", "log.md", "rounds/round_0000.json", "dashboard.json"} <= cycle_paths
    assert {"campaign.json"} <= campaign_paths


def test_file_returns_json_content(seeded_tenant: tuple[TestClient, str, str]) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    resp = client.get(
        f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/file",
        params={"scope": "cycle", "path": "dashboard.json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content_type"] == "json"
    assert json.loads(body["content"])["round"] == 3


def test_file_rejects_path_traversal(seeded_tenant: tuple[TestClient, str, str]) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    for bad in ("../etc/passwd", "/abs/path", "rounds\\..\\x"):
        resp = client.get(
            f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/file",
            params={"scope": "cycle", "path": bad},
        )
        assert resp.status_code == 400, f"expected 400 for {bad!r}, got {resp.status_code}"


def test_file_404_on_missing(seeded_tenant: tuple[TestClient, str, str]) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    resp = client.get(
        f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/file",
        params={"scope": "cycle", "path": "no_such.json"},
    )
    assert resp.status_code == 404


def test_file_oversize_returns_null_content(
    seeded_tenant: tuple[TestClient, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    # Force the size threshold below dashboard.json's actual size.
    monkeypatch.setattr(
        "promptpotter.presentation.api.routers.campaigns.files._MAX_PREVIEW_BYTES", 1
    )
    resp = client.get(
        f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/file",
        params={"scope": "cycle", "path": "dashboard.json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] is None
    assert body["content_type"] == "text"


def test_dataset_ingest_flows_through_draft_and_tenant_scope(
    seeded_tenant: tuple[TestClient, str, str],
) -> None:
    """``POST /datasets/ingest`` mints a tenant-scoped draft; sparse-edit lands
    on the same draft; cross-tenant lookup is 404 (existence-leak gate)."""
    from promptpotter.application.datasets.draft_campaign import DraftCampaignRegistry
    from promptpotter.domain.identity import TenantId

    client, *_ = seeded_tenant
    # TestClient runs lifespan via __enter__, so use the context manager to seed the registry.
    with client:
        r = client.post(
            "/api/v1/datasets/ingest",
            files={
                "file": (
                    "tickets.csv",
                    b"query,ground_truth\nq1,refund\nq2,bug_report\n",
                    "text/csv",
                ),
            },
        )
        assert r.status_code == 200, r.text
        draft = r.json()
        assert draft["n_samples"] == 2
        assert draft["slug"] == "tickets"
        assert draft["connector"] == "termnorm"
        assert draft["max_rounds"] == 5
        # Literal `query` / `ground_truth` headers auto-confirm the column
        # mapping deterministically (no LLM, no operator click).
        assert draft["headers"] == ["query", "ground_truth"]
        assert draft["field_provenance"]["column.query"] == "confirmed"
        assert draft["field_provenance"]["column.ground_truth"] == "confirmed"
        # Config (connector, max_rounds, …) is not gated — it carries a default
        # the operator edits, so it gets no provenance entry.
        assert "connector" not in draft["field_provenance"]
        # Sparse edit lands a mutation; response is the full post-mutation shape.
        edit = client.post(
            "/api/v1/commands/edit-draft-campaign",
            headers={"Idempotency-Key": "test-k1"},
            json={
                "kind": "edit-draft-campaign",
                "payload": {
                    "draft_id": draft["draft_id"],
                    "patch": {"raw_task_description": "label tickets", "max_rounds": 3},
                },
            },
        )
        assert edit.status_code == 200
        assert edit.json()["raw_task_description"] == "label tickets"
        assert edit.json()["max_rounds"] == 3
        # Cross-tenant lookup: same draft_id, different tenant → 404, not 403.
        registry: DraftCampaignRegistry = client.app.state.draft_campaigns  # type: ignore[attr-defined]
        assert registry.get(draft["draft_id"], tenant_id=TenantId("other-tenant")) is None
        # Header-agnostic ingest: non-literal columns are accepted (the
        # literal-column gate is gone), but the mapping lands UNSET for the
        # operator to confirm before mint.
        agnostic = client.post(
            "/api/v1/datasets/ingest",
            files={"file": ("other.csv", b"foo,bar\n1,2\n", "text/csv")},
        )
        assert agnostic.status_code == 200, agnostic.text
        ag = agnostic.json()
        assert ag["headers"] == ["foo", "bar"]
        assert ag["field_provenance"]["column.query"] == "unset"
        assert ag["field_provenance"]["column.ground_truth"] == "unset"
        # Mint is gated: unset column mapping → 422 origin_incomplete (the
        # checklist fires before the backend preflight, so no network touch).
        blocked = client.post(
            "/api/v1/commands/mint-campaign-from-draft",
            headers={"Idempotency-Key": "test-gate-1"},
            json={
                "kind": "mint-campaign-from-draft",
                "payload": {"draft_id": ag["draft_id"]},
            },
        )
        assert blocked.status_code == 422, blocked.text
        gate = blocked.json()  # flat ErrorEnvelope: {error, message, details}
        assert gate["error"] == "origin_incomplete"
        # Closed set: unset columns + the once-hidden `task_description` (no
        # default framing) gate; the config knobs auto-confirm from templates.
        assert {g["field"] for g in gate["details"]["gaps"]} == {
            "column.query",
            "column.ground_truth",
            "task_description",
        }
        # Confirming the mapping via edit-draft-campaign flips those fields'
        # provenance (task_description still gates until stated).
        confirmed = client.post(
            "/api/v1/commands/edit-draft-campaign",
            headers={"Idempotency-Key": "test-gate-2"},
            json={
                "kind": "edit-draft-campaign",
                "payload": {
                    "draft_id": ag["draft_id"],
                    "patch": {"column_query": "foo", "column_ground_truth": "bar"},
                },
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["field_provenance"]["column.query"] == "confirmed"
        assert confirmed.json()["field_provenance"]["column.ground_truth"] == "confirmed"


def test_mint_from_draft_503_preserves_draft_when_backend_unreachable(
    seeded_tenant: tuple[TestClient, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend-down preflight on ``mint-campaign-from-draft`` must fail BEFORE
    ``commit_draft`` runs — so the operator can fix the backend and retry
    without re-uploading. Surfaces as HTTP 503 with the structured
    ``backend_unreachable`` error code (not 422 ``payload_invalid``).

    Preflight is a ``Connector`` contract: swap the registered termnorm
    connector for a sibling whose ``preflight`` raises
    :class:`BackendUnreachableError`, exercising the same code path the real
    probe would hit on a TCP failure."""
    from dataclasses import replace

    from promptpotter import connectors
    from promptpotter.connectors import BackendUnreachableError

    async def _unreachable(backend_url: str) -> None:
        raise BackendUnreachableError(
            backend_type="termnorm",
            backend_url=backend_url,
            detail="connection refused",
        )

    failing = replace(connectors.CONNECTORS["termnorm"], preflight=_unreachable)
    monkeypatch.setitem(connectors.CONNECTORS, "termnorm", failing)

    client, *_ = seeded_tenant
    with client:
        r = client.post(
            "/api/v1/datasets/ingest",
            files={
                "file": (
                    "tickets.csv",
                    b"query,ground_truth\nq1,refund\nq2,bug_report\n",
                    "text/csv",
                ),
            },
        )
        assert r.status_code == 200, r.text
        draft = r.json()

        # State the framing so the origin gate passes and we reach the backend
        # preflight (literal columns already auto-confirmed; config knobs seed
        # from templates). Without this, mint blocks at 422 before any network.
        client.post(
            "/api/v1/commands/edit-draft-campaign",
            headers={"Idempotency-Key": "test-mint-frame"},
            json={
                "kind": "edit-draft-campaign",
                "payload": {
                    "draft_id": draft["draft_id"],
                    "patch": {"raw_task_description": "label support tickets"},
                },
            },
        )

        # First mint: backend down → 503 with the structured code, NOT a 422.
        mint = client.post(
            "/api/v1/commands/mint-campaign-from-draft",
            headers={"Idempotency-Key": "test-mint-1"},
            json={
                "kind": "mint-campaign-from-draft",
                "payload": {"draft_id": draft["draft_id"]},
            },
        )
        assert mint.status_code == 503, mint.text
        envelope = mint.json()  # flat ErrorEnvelope: {error, message, details}
        assert envelope["error"] == "backend_unreachable"
        assert envelope["details"]["backend_type"] == "termnorm"
        assert envelope["details"]["draft_id"] == draft["draft_id"]

        # Retry: draft is preserved (not 404 "draft not found") — operator can
        # fix the backend and retry without re-uploading. Still 503 here because
        # the patch keeps the backend unreachable, but the draft lookup succeeded.
        retry = client.post(
            "/api/v1/commands/mint-campaign-from-draft",
            headers={"Idempotency-Key": "test-mint-2"},
            json={
                "kind": "mint-campaign-from-draft",
                "payload": {"draft_id": draft["draft_id"]},
            },
        )
        assert retry.status_code == 503, retry.text
        assert retry.json()["error"] == "backend_unreachable"


def test_open_existing_origin_mints_canonical_without_cloning(
    seeded_tenant: tuple[TestClient, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening an already-valid origin (demo / benchmark / owned) must mint a
    campaign against the canonical dataset_name — never uniquify into a
    ``{slug}-N`` clone, never materialize a new ``datasets/`` folder. Re-running
    is a sibling campaign under the one origin, not a fresh dataset.

    Guards the root fix: the draft is derived (``source_file = "dataset:{slug}"``),
    so ``mint-campaign-from-draft`` skips ``commit_draft`` and mints directly.
    """
    from dataclasses import replace
    from types import SimpleNamespace

    from promptpotter import connectors
    from promptpotter.application.jobs import launcher

    client, *_ = seeded_tenant
    stores = app.dependency_overrides[build_stores_from_identity]()

    # Seed a tenant-owned Origin — already complete on disk, so opening it is the
    # "existing origin" path (same branch demo/benchmark take).
    ds_dir = stores.tenant_datasets.dataset_dir("email-tagging")
    ds_dir.mkdir(parents=True, exist_ok=True)
    (ds_dir / "cache.json").write_text(
        json.dumps({"items": [{"query": "q1", "ground_truth": "a1"}]}), encoding="utf-8"
    )
    (ds_dir / "pipeline.json").write_text(
        json.dumps({"backend_type": "termnorm", "name": "email-tagging"}), encoding="utf-8"
    )
    (ds_dir / "campaign.json").write_text(
        json.dumps(
            {
                "campaign_config": {
                    "dataset_name": "email-tagging",
                    "optimization": {
                        "max_rounds": 5,
                        "improvement_threshold": 0.0,
                        "degradation_threshold": 0.0,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    (ds_dir / "task_description.md").write_text("tag emails", encoding="utf-8")
    assert stores.tenant_datasets.list_slugs() == ["email-tagging"]

    # Opt out of the network preflight + replace the heavy mint with a stub that
    # records the dataset_name it was asked to run.
    no_preflight = replace(connectors.CONNECTORS["termnorm"], preflight=None)
    monkeypatch.setitem(connectors.CONNECTORS, "termnorm", no_preflight)
    minted_against: list[str] = []

    async def _stub_mint(*, dataset_name: str, **_kw: object):
        minted_against.append(dataset_name)
        return f"{dataset_name}__deadbeef", "cycle_x", SimpleNamespace(job_id="job-x")

    monkeypatch.setattr(launcher, "mint_campaign_command", _stub_mint)

    with client:
        # Open the existing origin → canonical slug, NOT email-tagging-2.
        draft = client.post("/api/v1/datasets/email-tagging/draft")
        assert draft.status_code == 200, draft.text
        assert draft.json()["slug"] == "email-tagging"

        mint = client.post(
            "/api/v1/commands/mint-campaign-from-draft",
            headers={"Idempotency-Key": "test-derived-mint"},
            json={
                "kind": "mint-campaign-from-draft",
                "payload": {"draft_id": draft.json()["draft_id"]},
            },
        )
        assert mint.status_code == 200, mint.text
        assert mint.json()["campaign_id"] == "email-tagging__deadbeef"

    # The mint ran against the canonical origin, and NO clone folder appeared.
    assert minted_against == ["email-tagging"]
    assert stores.tenant_datasets.list_slugs() == ["email-tagging"]


def test_dataset_listing_gates_benchmarks_on_capability(
    seeded_tenant: tuple[TestClient, str, str],
) -> None:
    """``GET /datasets`` is identity-scoped — web tenants (no
    ``datasets.benchmarks.read`` capability) MUST NOT see the install's
    repo-root benchmarks. The bleed-through fix the M13 ingest arc closes."""
    client, *_ = seeded_tenant
    r = client.get("/api/v1/datasets")
    assert r.status_code == 200
    entries = r.json()["datasets"]
    tiers = {e["tier"] for e in entries}
    assert "benchmark" not in tiers, (
        "default identity must not see benchmarks unless PROMPTPOTTER_ADMIN=1"
    )


def test_optimizer_pipeline_returns_view_topology() -> None:
    """``/optimizer-pipeline`` must expose the bundled ``view`` block (nodes +
    edges) — what the webapp renders the workflow from."""
    client = TestClient(app)
    resp = client.get("/api/v1/optimizer-pipeline")
    assert resp.status_code == 200
    body = resp.json()
    assert "view" in body
    view = body["view"]
    assert {"nodes", "edges"} <= view.keys()
    node_ids = {n["id"] for n in view["nodes"]}
    # Must include the L1 inner-loop trio + scoring + IO endpoints.
    assert {"input", "l1_generate", "l1_score", "l1_critique", "output"} <= node_ids


# --- Connector protocol — wire payload + session lifecycle pluggability ---
# (merged from test_connector_protocol.py — same external-wire bucket.)


def test_termnorm_wire_adapter_shapes_node_config() -> None:
    """TermNorm adapter projects pipeline_params to {query, steps, node_config},
    dropping non-dict per-node values (backend contract is a per-node config dict)."""
    payload = termnorm_wire_adapter(
        "what is X?",
        {
            "steps": ["fuzzy_matching", "entity_profiling"],
            "entity_profiling": {"prompt": "rank by relevance"},
            "fuzzy_matching": {"max_results": 10},
        },
    )
    assert payload["query"] == "what is X?"
    assert payload["steps"] == ["fuzzy_matching", "entity_profiling"]
    assert payload["node_config"] == {
        "entity_profiling": {"prompt": "rank by relevance"},
        "fuzzy_matching": {"max_results": 10},
    }
    # Non-dict per-node values are dropped — backend contract is per-node config dict.
    dropped = termnorm_wire_adapter("q", {"steps": ["x"], "x": {"k": "v"}, "garbage": "str"})
    assert dropped["node_config"] == {"x": {"k": "v"}}


def test_backend_client_uses_custom_wire_adapter() -> None:
    """BackendClient accepts a custom WireAdapter and uses it on run_query."""
    captured: dict[str, Any] = {}

    def alt_wire(query: str, pipeline_params: dict[str, Any] | None) -> dict[str, Any]:
        captured["query"] = query
        captured["pp"] = pipeline_params
        return {"prompt": query, "options": pipeline_params or {}}

    client = BackendClient(
        "http://example.invalid",
        wire_adapter=alt_wire,
        session=TermNormSession(),
    )
    payload = client._wire_adapter("hello", {"k": "v"})
    assert payload == {"prompt": "hello", "options": {"k": "v"}}
    assert captured == {"query": "hello", "pp": {"k": "v"}}


async def test_termnorm_session_idempotent_set_terms() -> None:
    """Identical terms shouldn't re-POST /sessions — idempotency contract."""

    class _StubResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"status": "ok"}

    posted: list[Any] = []

    class _StubHttp:
        async def post(self, *args: Any, **kwargs: Any) -> _StubResponse:
            posted.append((args, kwargs))
            return _StubResponse()

    sess = TermNormSession()
    await sess.set_terms(_StubHttp(), "http://x", ["a", "b"])  # type: ignore[arg-type]
    assert len(posted) == 1
    result = await sess.set_terms(_StubHttp(), "http://x", ["a", "b"])  # type: ignore[arg-type]
    assert result["status"] == "already_initialized"
    assert len(posted) == 1


# ===========================================================================
# §9. Authored-dataset reader — full node-block preservation + validation
# ===========================================================================

from promptpotter.application.datasets import read_authored_dataset  # noqa: E402


def test_read_authored_dataset_preserves_full_node_blocks_and_validates(tmp_path: Path) -> None:
    """The canonical reader behind the CLI ``new`` config-read, the launcher
    web-launch, and ``draft_from_dataset`` returns the WHOLE ``nodes.{name}``
    sub-blocks — collapsing to ``nodes.*.config`` would silently drop a
    committed draft's ``optimizer.param_allowed_values`` locks. Validation +
    backend-type lowercasing ride the same canonical case.
    """
    (tmp_path / "campaign.json").write_text(
        json.dumps(
            {
                "campaign_config": {
                    "scoring": "exact_match(predicted, ground_truth)",
                    "optimization": {
                        "max_rounds": 5,
                        "improvement_threshold": 0.01,
                        "degradation_threshold": 0.05,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "pipeline.json").write_text(
        json.dumps(
            {
                "backend_type": "TermNorm",
                "nodes": {
                    "llm_only": {
                        "config": {"model": "openai/gpt-oss-20b"},
                        "optimizer": {"param_allowed_values": {"temperature": [0.0, 0.5]}},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "task_description.md").write_text("  Route the ticket.  ", encoding="utf-8")

    authored = read_authored_dataset(tmp_path)

    # The optimizer lock must survive — not dropped to nodes.*.config.
    assert authored.pipeline_nodes["llm_only"]["optimizer"] == {
        "param_allowed_values": {"temperature": [0.0, 0.5]}
    }
    assert authored.backend_type == "termnorm"  # lowercased
    assert authored.task_description == "Route the ticket."  # stripped
    assert (
        authored.campaign_config.optimization.max_rounds == 5
    )  # campaign_config unwrapped + validated
