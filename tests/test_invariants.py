"""Structural invariants — artifact parity + hexagonal layer-import rules.

Two named invariants:
  1. ``LiveDashboardView`` lifecycle produces every per-cycle and
     per-session artifact in ``CAMPAIGN_ARTIFACTS`` / ``SESSION_ARTIFACTS``.
     Campaign artifacts split into ``ROOT_TELEMETRY_ARTIFACTS`` (shared
     across forks) and ``PER_CYCLE_OPERATOR_ARTIFACTS`` (per-cycle frozen
     records). Internals (``.runtime/``) live under that umbrella;
     ``ledger.jsonl`` / ``streams/`` / ``.cache/`` MUST NOT exist next to
     operator files. ``FileSink`` Langfuse shadow uses camelCase fields and
     nests node spans under round spans via ``parentObservationId``.
  2. Hexagonal runtime imports: ``domain/`` is a sink (imports nothing),
     ``application/intelligence/`` MUST NOT import from
     ``application/optimization/``, and ``infrastructure/`` MUST NOT
     import application/intelligence/optimization. ``cycle.py`` does NOT
     import prompt-surface modules at runtime (those modules import cycle;
     a back-edge re-introduces the cycle). The ``KNOWN_VIOLATIONS``
     allowlist must stay accurate — stale entries fail too.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
from pathlib import Path
from typing import Any, cast

import pytest

# ===========================================================================
# Artifact parity
# ===========================================================================

# Per-family-root artifacts (telemetry stream, shared across forks).
ROOT_TELEMETRY_ARTIFACTS = {
    "dashboard.json",
}

# Per-cycle operator-facing artifacts (frozen audit + drill-in dirs the
# operator reads directly).
PER_CYCLE_OPERATOR_ARTIFACTS = {
    "index.json",
    "log.md",
    "review.md",
    # Subdirs are conditional — only present when content was produced.
    # The minimum-required set asserted by ``test_emitter_produces_all_artifacts``
    # is the three top-level files.
}

# Per-cycle internal umbrella — opaque to operators. Holds the ledger spine,
# pobb streams, the round/candidate caches, and rewind sweepup. Existence
# of the umbrella dir is the contract; the contents are projection-owned.
PER_CYCLE_INTERNAL_UMBRELLA = ".runtime"

# Sibling-group dirs that may appear at the family root, each holding
# nested fork cycle dirs.
SIBLING_GROUP_DIRS = {"forks", "diag", "sweeps"}

# Combined campaign-tree contract — what an un-forked cycle dir must contain
# at minimum (operator-facing files only; the internal umbrella is asserted
# separately).
CAMPAIGN_ARTIFACTS = ROOT_TELEMETRY_ARTIFACTS | PER_CYCLE_OPERATOR_ARTIFACTS

# Per-session artifacts under ``sessions/{session_id}/``. ``session.json``
# is owned by SessionStore. ``journal.md`` / ``notes.md`` are NOT in this
# set — they're a notebook ↔ Claude exchange habit, not a contract; per
# tests/CLAUDE.md "UX affordances. Journal/notes/HITL exchange surface.
# It is a habit, not a contract." They appear lazily on first write.
SESSION_ARTIFACTS = {
    "session.json",
}


@pytest.fixture
def session_and_campaign_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Create a session dir + campaign dir + minimal session.json + index.json.

    Layout: ``{tmp_path}/sessions/{session_id}/`` and
    ``{tmp_path}/campaigns/{cycle_id}/``. ``tmp_path`` stands in for the
    tenant root.
    """
    session_id = "s_test1234"
    cycle_id = "cycle_test_abc"
    sdir = tmp_path / "sessions" / session_id
    cdir = tmp_path / "campaigns" / cycle_id
    sdir.mkdir(parents=True)
    cdir.mkdir(parents=True)

    (sdir / "session.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "phase": "optimizing",
            }
        )
    )
    (cdir / "index.json").write_text(
        json.dumps(
            {
                "campaign_id": cycle_id,
                "backend_id": "test_backend",
                "parent_session_id": session_id,
                "rounds": [],
                "n_rounds": 0,
            }
        )
    )
    return sdir, cdir


def test_artifact_sets_are_disjoint_and_well_formed() -> None:
    """CAMPAIGN_ARTIFACTS and SESSION_ARTIFACTS must never overlap; each
    fixed key must land in the expected set. The two campaign bands
    (``ROOT_TELEMETRY_ARTIFACTS`` and ``PER_CYCLE_OPERATOR_ARTIFACTS``) must
    also be disjoint — telemetry is not per-cycle, audit is not at root.
    Sibling-group dirs are not artifacts — they hold nested cycles."""
    assert CAMPAIGN_ARTIFACTS.isdisjoint(SESSION_ARTIFACTS)
    assert ROOT_TELEMETRY_ARTIFACTS.isdisjoint(PER_CYCLE_OPERATOR_ARTIFACTS)
    assert SIBLING_GROUP_DIRS.isdisjoint(CAMPAIGN_ARTIFACTS)
    assert PER_CYCLE_INTERNAL_UMBRELLA not in CAMPAIGN_ARTIFACTS
    assert {"session.json"} <= SESSION_ARTIFACTS
    assert {"dashboard.json"} <= ROOT_TELEMETRY_ARTIFACTS
    assert {"index.json", "log.md", "review.md"} <= PER_CYCLE_OPERATOR_ARTIFACTS
    assert {"forks", "diag", "sweeps"} == SIBLING_GROUP_DIRS


def test_emitter_produces_all_artifacts(session_and_campaign_dirs: tuple[Path, Path]) -> None:
    """Emitter lifecycle must produce all CAMPAIGN_ARTIFACTS in the campaign
    dir and ensure all SESSION_ARTIFACTS in the session dir."""
    from types import SimpleNamespace

    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.optimization.cycle import Cycle, TrackingState
    from promptpotter.domain.cycle_paths import RootCycleDir
    from promptpotter.domain.phases import PhaseEvent
    from promptpotter.domain.results import CycleResult, RoundResult
    from promptpotter.domain.run_records import PhaseRecord, SnapshotRecord
    from promptpotter.infrastructure.projections import LiveDashboardView
    from promptpotter.presentation.views.view_factories import (
        from_phase_event,
        view_to_wire_dict,
    )

    session_dir, campaign_dir = session_and_campaign_dirs
    config = CampaignConfig(
        optimization={
            "max_rounds": 5,
            "l1_patience": 3,
            "improvement_threshold": 0.01,
            "max_failures": 15,
            "degradation_threshold": 0.4,
        }
    )
    emitter = LiveDashboardView(
        RootCycleDir(campaign_dir),
        session_dir,
        l1_patience=3,
        n_variants=5,
        sp_budget_ttest=20,
    )

    # ``RunCallbacks`` would call ``from_phase_event`` once per event on a
    # shared ctx, serialise via ``view_to_wire_dict``, and feed PhaseRecord records
    # to the ledger; subscribers route them via ``on_record``. Mirror that here.
    phase_ctx: dict = {}

    def fire(event: PhaseEvent) -> None:
        view = view_to_wire_dict(from_phase_event(event, phase_ctx))
        emitter.on_record(
            PhaseRecord(
                phase=str(event.phase),
                event=str(event.event),
                round=event.round,
                payload={"view": view, "data": event.data},
            ),
            0,
        )

    # Simulate a single round lifecycle.  The emitter only reads a handful of
    # fields off the ``env`` payload (cycle_id, scoring.scoring_set, obs,
    # resumed_from_round, pipeline_schema) so a SimpleNamespace is enough.
    # Cycle requires session+config; the emitter doesn't read them off
    # ``init_state`` here, so SimpleNamespace stand-ins are fine.
    init_state = Cycle(
        session=cast("Any", SimpleNamespace(pipeline_schema=None)),
        config=config,
        tracking=TrackingState(current_accuracy=0.5),
    )
    init_env = SimpleNamespace(
        state=SimpleNamespace(
            cycle_id="cycle_test_001",
            obs=None,
            resumed_from_round=0,
        ),
        scoring=SimpleNamespace(scoring_set=[], scorer_round_formula=None),
        pipeline_schema=None,
    )
    fire(
        PhaseEvent(
            phase="init",
            event="exit",
            round=0,
            data={"state": init_state, "env": init_env, "config": config},
        )
    )
    fire(
        PhaseEvent(
            phase="l1_generate",
            event="enter",
            round=0,
            data={"round": 0},
        )
    )
    fire(
        PhaseEvent(
            phase="l1_generate",
            event="exit",
            round=0,
            data={"candidates": [{"idx": 0, "pipeline_params_override": {}}]},
        )
    )
    fire(
        PhaseEvent(
            phase="l1_score",
            event="enter",
            round=0,
            data={},
        )
    )

    def fire_snapshot(event: str, payload: dict, **idx: int) -> None:
        emitter.on_record(
            SnapshotRecord(
                event=event,
                round=0,
                candidate_idx=idx.get("ci"),
                candidate_total=idx.get("ct"),
                sample_idx=idx.get("qi"),
                sample_total=idx.get("qt"),
                payload=payload,
            ),
            0,
        )

    # Simulate a query measurement
    fire_snapshot(
        "sample_scored",
        {
            "result": {
                "query": "test_query",
                "prediction": "test_pred",
                "hit": True,
                "cached": False,
                "pipeline_data": {"total_time": 0.1, "terminated_at": "llm_ranking"},
            },
        },
        ci=0,
        ct=1,
        qi=0,
        qt=2,
    )
    fire_snapshot(
        "sample_scored",
        {
            "result": {
                "query": "test_query_2",
                "prediction": "test_pred_2",
                "hit": False,
                "ground_truth_rank": 3,
                "n_candidates": 10,
                "cached": True,
                "pipeline_data": {"total_time": 0.05, "terminated_at": "llm_ranking"},
            },
        },
        ci=0,
        ct=1,
        qi=1,
        qt=2,
    )

    # Simulate candidate scoring
    fire_snapshot(
        "candidate_scored",
        {"scores": {"accuracy": 0.6, "hits": 1, "total": 2}},
        ci=0,
        ct=1,
    )

    # Simulate round complete
    round_result = RoundResult(
        round=0,
        label="C1",
        accuracy=0.6,
        hits=1,
        total=2,
        improved=True,
        prompt_fields={"instruction": "test"},
        candidates_scored=1,
    )
    emitter.on_record(
        PhaseRecord(
            phase="round",
            event="display",
            round=0,
            payload={"round_result": round_result, "l1_stall_count": 0},
        ),
        0,
    )

    # Mirror runner._finalize_run: fold the run summary into index.json::final
    # and render log.md + review.md from the index + round_data dump.
    from promptpotter.application.review import render_review_md
    from promptpotter.presentation.views.render_markdown import to_markdown
    from promptpotter.presentation.views.view_factories import from_disk_log

    final = CycleResult(
        rounds=[round_result],
        n_rounds=1,
        best_accuracy=0.6,
        best_round=0,
        baseline_accuracy=0.5,
        winner_prompt_fields={"instruction": "test"},
        stop_reason="max_rounds",
        started_at="2026-04-19T00:00:00+00:00",
        finished_at="2026-04-19T00:01:00+00:00",
        cycle_id="cycle_test_001",
    ).model_dump(exclude={"rounds"})
    index_path = campaign_dir / "index.json"
    index_data = json.loads(index_path.read_text(encoding="utf-8")) | {"final": final}
    index_path.write_text(json.dumps(index_data), encoding="utf-8")
    trial_dump = round_result.model_dump()
    (campaign_dir / "log.md").write_text(
        to_markdown(from_disk_log(index_data, [trial_dump])), encoding="utf-8"
    )
    (campaign_dir / "review.md").write_text(
        render_review_md(index_data, [trial_dump], round_audits=[None]), encoding="utf-8"
    )

    missing_campaign = [a for a in CAMPAIGN_ARTIFACTS if not (campaign_dir / a).exists()]
    assert not missing_campaign, f"Campaign-tree parity violated — missing: {missing_campaign}"
    missing_session = [a for a in SESSION_ARTIFACTS if not (session_dir / a).exists()]
    assert not missing_session, f"Session-tree parity violated — missing: {missing_session}"

    # Internals: ``.runtime/`` umbrella with ``cache/rounds/`` is created
    # by ``AuditTrailView.flush`` whenever a round records node
    # I/O. The legacy top-level ``ledger.jsonl`` / ``streams/`` /
    # ``.cache/`` paths must NOT exist next to operator files.
    assert not (campaign_dir / "ledger.jsonl").exists(), "ledger.jsonl moved under .runtime/"
    assert not (campaign_dir / ".cache").exists(), ".cache/ replaced by .runtime/cache/"
    assert not (campaign_dir / "streams").exists(), "streams/ moved under .runtime/"


def test_campaign_records_parent_session(session_and_campaign_dirs: tuple[Path, Path]) -> None:
    """Every campaign records its parent session id in index.json."""
    _session_dir, campaign_dir = session_and_campaign_dirs
    data = json.loads((campaign_dir / "index.json").read_text(encoding="utf-8"))
    assert data["parent_session_id"], "index.json must carry parent_session_id"


def test_observers_built_via_shared_helper() -> None:
    """Entry points MUST construct projections + callbacks via ``build_run_observers``.

    Direct ``RunCallbacks()``, ``AuditTrailView()``, ``LiveDashboardView()``,
    or ``PoBBStreamView()`` construction in ``presentation/`` is forbidden —
    a new entry point that bypasses the helper would create a divergent observer
    wiring (different bind order, missing subscribers, two-phase init regression).

    LiveDisplay is allowed because it's a presentation-layer concern that the
    helper accepts as input. The helper itself owns the ledger + audit + dashboard
    + pobb construction.
    """
    BANNED = {
        "RunCallbacks",
        "AuditTrailView",
        "LiveDashboardView",
        "PoBBStreamView",
    }
    repo_root = Path(__file__).resolve().parents[1]
    guarded_paths = [
        repo_root / "promptpotter" / "presentation" / "cli" / "campaign_runner.py",
        repo_root / "promptpotter" / "presentation" / "views" / "notebook_run.py",
    ]
    offenders: list[str] = []
    for src_path in guarded_paths:
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else None
            if name in BANNED:
                rel = src_path.relative_to(repo_root)
                offenders.append(f"{rel}:{node.lineno}:{name}")
    assert not offenders, (
        "Direct observer construction in presentation/ — route through "
        "application/optimization/observers.py::build_run_observers:\n" + "\n".join(offenders)
    )


def test_run_callbacks_requires_ledger() -> None:
    """RunCallbacks must be constructed with a ledger — no two-phase init.

    The pre-refactor ``RunListener`` allowed ``listener=None`` then late-bound the
    ledger via a setter, which buffered events in a dead path (the ledger was
    always bound before any event fired). Forbid the regression: dataclass
    construction without a ledger raises ``TypeError`` at boot.
    """
    from promptpotter.application.optimization.observers import RunCallbacks

    with pytest.raises(TypeError):
        RunCallbacks()  # type: ignore[call-arg]


def test_no_direct_artifact_writes_outside_stores() -> None:
    """Entry points and orchestrators MUST NOT write campaign artifacts directly.

    The persistence invariant says only Stores own atomic file ops on the
    campaign tree. cycle.py and the sweep paths used to bypass this with
    direct write_json/shutil.copyfile/Path.write_text calls into campaign
    dirs — silently passing the existence check above. AST-walk those
    modules and forbid the bypass; introducing a new direct write here
    fails the test with a file:line:func pointer.
    """
    BANNED_FUNCS = {"write_json", "write_text", "write_bytes", "copyfile", "copy", "copy2"}
    repo_root = Path(__file__).resolve().parents[1]
    guarded_paths = [
        repo_root / "promptpotter" / "application" / "optimization" / "cycle.py",
        repo_root / "promptpotter" / "presentation" / "cli" / "campaign_runner.py",
        repo_root / "promptpotter" / "application" / "sweep" / "sweep_runner.py",
    ]
    offenders: list[str] = []
    for src_path in guarded_paths:
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name: str | None
            if isinstance(fn, ast.Attribute):
                name = fn.attr
            elif isinstance(fn, ast.Name):
                name = fn.id
            else:
                name = None
            if name in BANNED_FUNCS:
                rel = src_path.relative_to(repo_root)
                offenders.append(f"{rel}:{node.lineno}:{name}")
    assert not offenders, (
        "Direct artifact writes detected — route through CampaignStore/SweepStore:\n"
        + "\n".join(offenders)
    )


def test_file_sink_wire_format_parity(tmp_path: Path) -> None:
    """FileSink's Langfuse shadow must be wire-format compatible: camelCase
    fields on observations/scores, and nested spans carry parentObservationId.

    A JSON from ``campaigns/{cycle_id}/langfuse/`` should be uploadable to
    Langfuse's ingestion API without a transform pass.
    """
    from promptpotter.infrastructure.tracing import (
        CampaignEnd,
        CampaignStart,
        FileSink,
        NodeEnd,
        NodeStart,
        RoundEnd,
        RoundStart,
    )

    tenant_root = tmp_path / "default"
    tenant_root.mkdir()
    sink = FileSink(str(tenant_root), backend_id="bk_test")

    cycle_id = "cycle_wire_parity"
    campaign_id = "cmp_wire_parity"

    sink.on_campaign_start(
        CampaignStart(
            campaign_id=campaign_id,
            config={"max_rounds": 1},
            baseline_accuracy=0.5,
            session_id=cycle_id,
        )
    )
    sink.on_round_start(RoundStart(campaign_id=campaign_id, round_num=0))
    sink.on_node_start(
        NodeStart(
            campaign_id=campaign_id,
            round_num=0,
            node_id="l1_generate",
            node_type="llm",
            as_type="generation",
            input_data={"prompt": "hello"},
        )
    )
    sink.on_node_end(
        NodeEnd(
            campaign_id=campaign_id,
            round_num=0,
            node_id="l1_generate",
            output_data={"text": "world"},
        )
    )
    sink.on_round_end(
        RoundEnd(
            campaign_id=campaign_id,
            round_num=0,
            accuracy=0.7,
            hits=7,
            total=10,
            improved=True,
            winner_prompt_fields_id="abc",
            candidate_scores=[{"candidate": "c0", "accuracy": 0.7}],
            next_action="continue",
        )
    )
    sink.on_campaign_end(
        CampaignEnd(
            campaign_id=campaign_id,
            best_accuracy=0.7,
            n_rounds=1,
            stop_reason="max_rounds",
            best_round=0,
        )
    )

    campaign_root = tenant_root / "campaigns" / cycle_id

    trace_files = list((campaign_root / "langfuse" / "traces").glob("*.json"))
    assert len(trace_files) == 1
    trace_id = json.loads(trace_files[0].read_text())["id"]

    obs_dir = campaign_root / "langfuse" / "observations" / trace_id
    observations = [json.loads(p.read_text()) for p in obs_dir.glob("*.json")]
    assert len(observations) == 2, "round + node observation expected (single obs per round)"

    for obs in observations:
        assert "traceId" in obs and obs["traceId"] == trace_id
        assert "startTime" in obs
        assert "endTime" in obs
        for snake in ("trace_id", "start_time", "end_time"):
            assert snake not in obs, f"snake_case field {snake!r} leaked into {obs['name']}"

    round_obs = next(o for o in observations if o["name"].startswith("round_"))
    node_obs = next(o for o in observations if o["name"] == "l1_generate")
    assert "parentObservationId" not in round_obs, "round span has no parent (trace-level)"
    assert node_obs["parentObservationId"] == round_obs["id"], "node must nest under round"

    score_files = list((campaign_root / "langfuse" / "scores").glob("*.jsonl"))
    assert score_files, "at least one score jsonl expected"
    scores = [json.loads(line) for p in score_files for line in p.read_text().splitlines() if line]
    assert scores, "at least one score entry expected"
    for score in scores:
        assert "traceId" in score and score["traceId"] == trace_id
        assert "dataType" in score
        for snake in ("trace_id", "data_type"):
            assert snake not in score, f"snake_case field {snake!r} leaked into score"


# ===========================================================================
# Hexagonal layer-import rule guard
# ===========================================================================

ROOT = pathlib.Path(__file__).parent.parent / "promptpotter"


# Documented runtime cross-layer imports. The codebase is currently clean
# at runtime; this allowlist exists so that any *intentional* future
# violation can be tracked here with a TODO pointer rather than slipping
# in silently. Stale entries fail the test, so the list cannot drift.
KNOWN_VIOLATIONS: frozenset[tuple[str, str]] = frozenset()


def _layer(rel_posix: str) -> str | None:
    """Map a source file path to its hexagonal layer."""
    if "/domain/" in rel_posix:
        return "domain"
    if "/application/intelligence/" in rel_posix:
        return "intelligence"
    if "/application/optimization/" in rel_posix:
        return "optimization"
    if "/application/" in rel_posix:
        return "application"
    if "/infrastructure/" in rel_posix:
        return "infrastructure"
    if "/presentation/" in rel_posix:
        return "presentation"
    return None


def _target_layer(module: str) -> str | None:
    """Map an imported promptpotter module to its hexagonal layer."""
    if module.startswith("promptpotter.domain"):
        return "domain"
    if module.startswith("promptpotter.application.intelligence"):
        return "intelligence"
    if module.startswith("promptpotter.application.optimization"):
        return "optimization"
    if module.startswith("promptpotter.application"):
        return "application"
    if module.startswith("promptpotter.infrastructure"):
        return "infrastructure"
    if module.startswith("promptpotter.presentation"):
        return "presentation"
    return None


def _is_violation(src: str, tgt: str) -> bool:
    """The runtime-import rules. Tightest at the bottom (domain), loosest at the top."""
    if src == "domain" and tgt != "domain":
        return True
    if src == "intelligence" and tgt == "optimization":
        return True
    return src == "infrastructure" and tgt in {"application", "intelligence", "optimization"}


class _RuntimeImports(ast.NodeVisitor):
    """Collect imports, skipping ``if TYPE_CHECKING:`` blocks."""

    def __init__(self) -> None:
        self.modules: list[str] = []

    def visit_If(self, node: ast.If) -> None:
        if "TYPE_CHECKING" in ast.unparse(node.test):
            for n in node.orelse:
                self.visit(n)
            return
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.modules.append(node.module)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.modules.append(alias.name)


def _scan_violations() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT.parent).as_posix()
        src_layer = _layer(rel)
        if src_layer is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        visitor = _RuntimeImports()
        visitor.visit(tree)
        for module in visitor.modules:
            tgt_layer = _target_layer(module)
            if tgt_layer is None:
                continue
            if _is_violation(src_layer, tgt_layer):
                found.add((rel, module))
    return found


_CYCLE_FORBIDDEN_PROMPT_SURFACE = frozenset(
    {
        "promptpotter.application.optimization.dispatch_hub",
        "promptpotter.application.optimization.l1_critique",
        "promptpotter.application.optimization.transitions",
        "promptpotter.application.optimization.escalation",
    }
)


def test_cycle_does_not_import_prompt_surface() -> None:
    """Cycle must not import from prompt-surface or escalation modules.

    The cycle ↔ pipeline back-edge (cycle.py importing L2/L3 strategies
    from pipeline.py at module top) was the structural smell that drove
    the C1 split. Reasserting it would re-introduce the circular
    workaround. Guard: cycle.py imports neither the surface compilers
    nor the escalation driver — escalation imports cycle, never the
    reverse.
    """
    cycle_path = ROOT / "application" / "optimization" / "cycle.py"
    tree = ast.parse(cycle_path.read_text(encoding="utf-8"))
    visitor = _RuntimeImports()
    visitor.visit(tree)
    forbidden = sorted(set(visitor.modules) & _CYCLE_FORBIDDEN_PROMPT_SURFACE)
    assert not forbidden, (
        "cycle.py must not import from prompt-surface modules at runtime — "
        "those modules import cycle. A back-edge re-introduces the cycle.\n"
        "Forbidden imports detected:\n  " + "\n  ".join(forbidden)
    )


def test_no_unexpected_runtime_layer_violations() -> None:
    found = _scan_violations()
    new = found - KNOWN_VIOLATIONS
    stale = KNOWN_VIOLATIONS - found
    assert not new, (
        "New runtime layer-import violations detected. "
        "Either fix the import, or — if intentional and pending a rework — add it "
        "to KNOWN_VIOLATIONS with a TODO pointer.\nNew violations:\n  "
        + "\n  ".join(f"{src}: {tgt}" for src, tgt in sorted(new))
    )
    assert not stale, (
        "KNOWN_VIOLATIONS contains entries that no longer occur in the source. "
        "Remove them to keep the allowlist accurate.\nStale entries:\n  "
        + "\n  ".join(f"{src}: {tgt}" for src, tgt in sorted(stale))
    )


# Sole permitted module for archive method-access — the §3.7 facade.
_ARCHIVE_FACADE_MODULE = "infrastructure/store/archive_views.py"
# Permitted same-layer access: the archive itself can call its own methods,
# and ``stores.archive`` exposure inside ``stores.py`` is part of the surface.
_ARCHIVE_INTERNAL_MODULES = frozenset(
    {
        "infrastructure/store/measurement_archive.py",
        "infrastructure/store/stores.py",
        "infrastructure/store/__init__.py",
    }
)
# Pattern catches ``store.archive.method(``, ``self.archive.method(``,
# ``cls.archive.method(``, plus the alias form ``= session.store.archive``
# which then enables ``archive.method(`` calls.
_ARCHIVE_DIRECT_PATTERNS = (
    re.compile(r"\b(?:store|self|cls)\.archive\.[a-zA-Z_]"),
    re.compile(r"=\s*\S+\.store\.archive\b"),
)


def test_no_direct_archive_access_outside_facade() -> None:
    """Every read/write of MeasurementArchive routes through ``archive_views``.

    The §3.7 facade (``infrastructure/store/archive_views.py``) is the sole
    gateway. Direct method calls (``store.archive.X(...)``) and aliasing
    (``archive = session.store.archive``) outside the facade are drift —
    multiple readers + writers of the database core without a gated entry
    is the same problem ``CycleEventLog`` solved for events. Same-layer
    archive internals (``measurement_archive.py``, ``stores.py``,
    ``__init__.py``) are exempt.
    """
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel == _ARCHIVE_FACADE_MODULE.removeprefix("infrastructure/").replace(
            "store/archive_views.py", ""
        ):
            pass  # narrowing handled below
        if rel in _ARCHIVE_INTERNAL_MODULES:
            continue
        if rel == "infrastructure/store/archive_views.py":
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in _ARCHIVE_DIRECT_PATTERNS:
            for match in pattern.finditer(text):
                # Strip docstring matches (heuristic: the matching line begins
                # with ``"`` or ``#`` after the line's leading whitespace).
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end = text.find("\n", match.start())
                line = text[line_start : line_end if line_end != -1 else None]
                stripped = line.lstrip()
                if stripped.startswith(("#", '"', "'", "*")):
                    continue
                offenders.append(
                    f"{rel}:{text[: match.start()].count(chr(10)) + 1}: {line.strip()}"
                )
    assert not offenders, (
        "Direct MeasurementArchive access outside the facade detected. "
        "Route through ``promptpotter.infrastructure.store.archive_views`` instead.\n"
        "Offenders:\n  " + "\n  ".join(offenders)
    )
