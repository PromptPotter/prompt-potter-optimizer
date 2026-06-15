"""Structural drift-locks — declarative source-scan tables.

The charter's architecture invariants that are enforced by scanning source (not
by exercising behavior) live here as data. Each ban is a row; the engine in
``_scan`` runs it. Add a lock = add a row.

These replace the per-lock ``rglob``/``ast.walk``/offender-list functions that
used to sprawl across ``test_invariants.py``.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from _scan import (
    ROOT,
    call_offenders,
    calls_missing_kwarg,
    iter_py,
    parse,
    raise_offenders,
    regex_offenders,
    runtime_imports,
)


@dataclass(frozen=True)
class RegexBan:
    """A forbidden source pattern, scoped to a subtree with an allowlist."""

    id: str
    patterns: tuple[str, ...]
    message: str
    under: str | None = None  # restrict to this subtree (None = all of promptpotter/)
    allow: frozenset[str] = field(default_factory=frozenset)  # rel paths exempt from the ban
    skip_comment_lines: bool = False


REGEX_BANS: tuple[RegexBan, ...] = (
    RegexBan(
        id="archive_facade",
        patterns=(r"\b(?:store|self|cls)\.archive\.[a-zA-Z_]", r"=\s*\S+\.store\.archive\b"),
        message=(
            "Direct MeasurementArchive access outside the facade — route through "
            "infrastructure/store/archive_views.py."
        ),
        allow=frozenset(
            {
                "infrastructure/store/measurement_archive.py",
                "infrastructure/store/stores.py",
                "infrastructure/store/__init__.py",
                "infrastructure/store/archive_views.py",
            }
        ),
        skip_comment_lines=True,
    ),
    RegexBan(
        id="control_hooks_seam",
        patterns=(r"\.(?:pause_check|stop_check)\s*=(?!=)",),
        message=(
            "pause_check/stop_check assigned outside application/runner/entry.py — "
            "control-local wiring must stay central so every launch path polls the flags."
        ),
        allow=frozenset({"application/runner/entry.py"}),
    ),
    RegexBan(
        id="cli_identity_resolver",
        patterns=(r"default_identity\(\s*tenant_id\s*=\s*getattr\(\s*args",),
        message=(
            "CLI identity resolved off args via default_identity() — use "
            "registered_or_default_identity so resume/sweep honour the registered "
            "developer's one workspace."
        ),
    ),
    RegexBan(
        id="api_benchmarks_root",
        patterns=(r"DEFAULT_DATASETS_ROOT|\.benchmarks_root\b",),
        message=(
            "presentation/api/ reads the benchmarks root directly — route dataset "
            "access through store/dataset_access.py so the capability gate can't be skipped."
        ),
        under="presentation/api",
    ),
    RegexBan(
        id="nurse_target_retired",
        patterns=(r"\bnurse_target\b",),
        message=(
            "nurse_target (producer-keyed routing) is retired. A wound's owner is "
            "structural: a RuntimeFailure carries an `owner: NurseOwner` field (the one "
            "wound whose owner varies — L1 vs OPERATOR); ValidationFailure is always L1 "
            "and a guard-breach ValidatorOutcome always L3 via the escalate_l2 stream. "
            "Don't re-introduce a producer→nurse field."
        ),
    ),
)


@pytest.mark.parametrize("ban", REGEX_BANS, ids=[b.id for b in REGEX_BANS])
def test_forbidden_source_regex(ban: RegexBan) -> None:
    compiled = [re.compile(p) for p in ban.patterns]
    offenders: list[str] = []
    for rel, path in iter_py(ban.under):
        if rel in ban.allow:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in compiled:
            for lineno in regex_offenders(pattern, text, skip_comment_lines=ban.skip_comment_lines):
                offenders.append(f"{rel}:{lineno}")
    assert not offenders, f"[{ban.id}] {ban.message}\n  " + "\n  ".join(offenders)


@dataclass(frozen=True)
class CallBan:
    """A forbidden call name, scoped either to explicit files or a subtree.

    ``match``: ``"any"`` (``foo()`` or ``x.foo()``), ``"attr"`` (only ``x.foo()``),
    or ``"name"`` (only ``foo()`` — i.e. a bare constructor/function call).
    """

    id: str
    names: frozenset[str]
    message: str
    match: str = "any"
    paths: tuple[str, ...] = ()  # explicit rel paths; empty ⇒ glob under `under`
    under: str | None = None
    allow: frozenset[str] = field(default_factory=frozenset)


_OBSERVERS = frozenset({"RunCallbacks", "AuditTrailView", "LiveDashboardView", "PoBBStreamView"})

CALL_BANS: tuple[CallBan, ...] = (
    CallBan(
        id="direct_artifact_writes",
        names=frozenset({"write_json", "write_text", "write_bytes", "copyfile", "copy", "copy2"}),
        message="Direct artifact writes — route through CampaignStore/SweepStore.",
        paths=(
            "application/optimization/cycle.py",
            "presentation/cli/campaign_runner.py",
            "application/sweep/sweep_runner.py",
        ),
    ),
    CallBan(
        id="entry_point_observer_ctors",
        names=_OBSERVERS,
        message="Observers constructed by hand — route through build_run_observers.",
        match="name",
        paths=("presentation/cli/campaign_runner.py", "presentation/views/notebook_run.py"),
    ),
    CallBan(
        id="entry_point_mint_prologue",
        names=frozenset({"auto_mint_session"}),
        message="Fresh mint by hand — route through jobs/mint.py::prepare_fresh_cycle.",
        match="name",
        paths=("presentation/cli/commands/new.py", "application/jobs/launcher.py"),
    ),
    CallBan(
        id="llm_chat_funnel",
        names=frozenset({"chat"}),
        message="Direct ``.chat()`` outside dispatch/llm_call/call.py (pre-flight gate Q8).",
        match="attr",
        allow=frozenset({"application/optimization/dispatch/llm_call/call.py"}),
    ),
    CallBan(
        id="mask_read_only",
        names=frozenset({"write_json", "write_text", "write_bytes"}),
        message="The mask package persists nothing — it is a read-only projection "
        "(no mask state on disk; decision #1 in docs/specs/mask-projection.md).",
        under="application/mask",
    ),
    CallBan(
        id="mask_reeval_seam",
        names=frozenset({"compute_composite_fitness", "compile_round_scorer"}),
        message="Mask re-evaluation math must route through value_with_mask_applied — "
        "no parallel scoring kernel in the mask package.",
        under="application/mask",
    ),
)


@pytest.mark.parametrize("ban", CALL_BANS, ids=[b.id for b in CALL_BANS])
def test_forbidden_calls(ban: CallBan) -> None:
    targets = (
        [(p, ROOT / p) for p in ban.paths]
        if ban.paths
        else [(rel, path) for rel, path in iter_py(ban.under) if rel not in ban.allow]
    )
    offenders: list[str] = []
    for rel, path in targets:
        for lineno in call_offenders(path, ban.names, ban.match):
            offenders.append(f"{rel}:{lineno}")
    assert not offenders, f"[{ban.id}] {ban.message}\n  " + "\n  ".join(offenders)


def test_no_raw_http_exception_in_api() -> None:
    """The API layer raises no ``HTTPException`` — every error is a typed PotterError
    mapped to the flat ErrorEnvelope at the single main.py seam."""
    offenders = [
        f"{rel}:{lineno}"
        for rel, path in iter_py("presentation/api")
        for lineno in raise_offenders(path, "HTTPException")
    ]
    assert not offenders, "raw HTTPException in presentation/api/ — raise a PotterError:\n  " + (
        "\n  ".join(offenders)
    )


# ── Hexagonal layer-import rules ────────────────────────────────────────────
# Allowlist for intentional runtime cross-layer imports; stale entries fail.
KNOWN_VIOLATIONS: frozenset[tuple[str, str]] = frozenset()


def _layer(rel_posix: str) -> str | None:
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


def _is_layer_violation(src: str, tgt: str) -> bool:
    if src == "domain" and tgt != "domain":
        return True
    if src == "intelligence" and tgt == "optimization":
        return True
    return src == "infrastructure" and tgt in {"application", "intelligence", "optimization"}


def test_runtime_layer_imports() -> None:
    """No runtime import crosses a forbidden hexagonal edge (domain→*, intelligence→
    optimization, infrastructure→app/intelligence/optimization)."""
    found: set[tuple[str, str]] = set()
    for rel, path in iter_py():
        full = f"promptpotter/{rel}"
        src_layer = _layer(full)
        if src_layer is None:
            continue
        for module in runtime_imports(path):
            tgt_layer = _target_layer(module)
            if tgt_layer is not None and _is_layer_violation(src_layer, tgt_layer):
                found.add((full, module))
    new = found - KNOWN_VIOLATIONS
    stale = KNOWN_VIOLATIONS - found
    assert not new, "New runtime layer-import violations:\n  " + "\n  ".join(
        f"{src}: {tgt}" for src, tgt in sorted(new)
    )
    assert not stale, "Stale KNOWN_VIOLATIONS:\n  " + "\n  ".join(
        f"{src}: {tgt}" for src, tgt in sorted(stale)
    )


_CYCLE_FORBIDDEN_PROMPT_SURFACE = frozenset(
    {
        "promptpotter.application.optimization.dispatch.hub",
        "promptpotter.application.optimization.l1.critique",
        "promptpotter.application.optimization.transitions",
        "promptpotter.application.optimization.escalation",
    }
)


def test_cycle_does_not_import_prompt_surface() -> None:
    """``cycle.py`` must not import prompt-surface/escalation — escalation imports cycle."""
    cycle_path = ROOT / "application" / "optimization" / "cycle.py"
    forbidden = sorted(set(runtime_imports(cycle_path)) & _CYCLE_FORBIDDEN_PROMPT_SURFACE)
    assert not forbidden, "cycle.py back-edge to prompt-surface modules:\n  " + "\n  ".join(
        forbidden
    )


# ── Bespoke AST locks ───────────────────────────────────────────────────────
# These four don't reduce to a regex/call row — each tests a *predicate* over a
# call's kwargs or a specific function's control flow. They still ride the one
# parse engine in ``_scan``; they just can't be expressed as a table row.


def test_score_search_point_callers_pass_on_sample_scored() -> None:
    """Every ``score_search_point()`` call wires ``on_sample_scored`` explicitly —
    silence is opt-in (pass ``None``), never accidental."""
    offenders = [
        f"{rel}:{lineno}"
        for rel, path in iter_py()
        for lineno in calls_missing_kwarg(path, "score_search_point", "on_sample_scored")
    ]
    assert not offenders, (
        "Silent measure_sample regression — score_search_point() must pass "
        "on_sample_scored=... explicitly (wire a callback, or pass None for "
        "intentional silence):\n  " + "\n  ".join(offenders)
    )


# ``record_decision(<ledger>, <kind>, ...)`` — the second arg must be a typed
# ``ResumeCheckpointKind.*``, never a bare string. ``(?<!def )`` skips the def.
_RECORD_DECISION = re.compile(
    r"(?<!def\ )record_decision\s*\(\s*[^,()\[\]]+,\s*(?P<kind>[^,)]+)",
    re.DOTALL,
)


def test_record_decision_uses_typed_kind() -> None:
    """``record_decision`` calls pass a ``ResumeCheckpointKind``, not a bare string."""
    offenders: list[str] = []
    for rel, path in iter_py():
        text = path.read_text(encoding="utf-8")
        if "record_decision(" not in text:
            continue
        for match in _RECORD_DECISION.finditer(text):
            kind = match.group("kind").strip()
            if not kind.startswith("ResumeCheckpointKind."):
                offenders.append(f"{rel}: {kind!r}")
    assert not offenders, "bare-string decision kinds found:\n  " + "\n  ".join(offenders)


# The two canonical I/O seams and their sanctioned exceptions. spend.py's rate
# cache (~/.promptpotter/rates.json) is a deliberately-compact (indent=0) global
# blob — not a campaign artifact, so it stays out of the pretty/atomic seam.
_IO_SEAM = "infrastructure/store/base.py"  # write_json/read_json/append_jsonl/_atomic_*
_CLOCK_SEAM = "shared/clock.py"  # utcnow_iso — sole UTC-timestamp minter
_COMPACT_CACHE = "shared/spend.py"


def _is_json_call(node: ast.AST, attr: str) -> bool:
    """True if *node* is ``json.<attr>(...)``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "json"
    )


def test_no_hand_rolled_io_seam_bypass() -> None:
    """No site re-implements a canonical I/O seam — the seam-enforcement lock.

    ``os.replace`` (atomic rename missing the seam's WinError-5 retry +
    long-path hardening), inline ``now().isoformat()`` (drifts from
    ``utcnow_iso``'s ``...Z``), ``json.dump``/``load`` on a handle (no
    long-path/atomic swap), and ``write_text(json.dumps(...))`` (non-atomic
    clobber) all bypass the blessed helper. Route writes through
    ``write_json``/``write_text``/``append_jsonl``, reads through ``read_json``,
    timestamps through ``utcnow_iso``.
    """
    offenders: list[str] = []
    for rel, path in iter_py():
        if rel in (_IO_SEAM, _CLOCK_SEAM):
            continue
        for node in ast.walk(parse(path)):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if (
                isinstance(fn, ast.Attribute)
                and fn.attr == "replace"
                and isinstance(fn.value, ast.Name)
                and fn.value.id == "os"
            ):
                offenders.append(f"{rel}:{node.lineno}: os.replace — use write_json/write_text")
            if (
                isinstance(fn, ast.Attribute)
                and fn.attr == "isoformat"
                and isinstance(fn.value, ast.Call)
                and isinstance(fn.value.func, ast.Attribute)
                and fn.value.func.attr == "now"
            ):
                offenders.append(
                    f"{rel}:{node.lineno}: inline now().isoformat() — use utcnow_iso()"
                )
            if _is_json_call(node, "dump") or _is_json_call(node, "load"):
                offenders.append(
                    f"{rel}:{node.lineno}: json.dump/load on a handle — use write_json/read_json"
                )
            if (
                rel != _COMPACT_CACHE
                and isinstance(fn, ast.Attribute)
                and fn.attr == "write_text"
                and node.args
                and _is_json_call(node.args[0], "dumps")
            ):
                offenders.append(
                    f"{rel}:{node.lineno}: write_text(json.dumps(...)) — use write_json"
                )
    assert not offenders, (
        "Hand-rolled bypass of a canonical I/O seam — route through "
        "infrastructure/store/base.py (write_json/read_json/write_text/append_jsonl) "
        "or shared/clock.py (utcnow_iso):\n  " + "\n  ".join(offenders)
    )


def test_run_round_loop_continue_routes_through_close_round() -> None:
    """Every ``continue`` in the round-iteration ``while`` of ``run_round_loop``
    calls ``close_round`` (or ``post_round``) — no branch skips round close."""
    tree = parse(ROOT / "application" / "runner" / "loop.py")
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_round_loop"
    )
    # Outermost while is the round-iteration loop; a nested pause-wait while has
    # no ``continue`` of its own, so the invariant only binds the outer branches.
    while_nodes = [n for n in ast.walk(fn) if isinstance(n, ast.While)]
    assert while_nodes, "expected a round-iteration while in run_round_loop"
    round_loop = while_nodes[0]
    sanctioned = {"close_round", "post_round"}

    def _calls_sanctioned(stmt: ast.AST) -> bool:
        return any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in sanctioned
            for node in ast.walk(stmt)
        )

    offenders = [
        f"line {stmt.lineno}: ``continue`` without close_round/post_round call"
        for stmt in round_loop.body
        if isinstance(stmt, ast.If)
        and any(isinstance(s, ast.Continue) for s in stmt.body)
        and not _calls_sanctioned(stmt)
    ]
    assert not offenders, "round-loop branches must route through close_round:\n  " + "\n  ".join(
        offenders
    )


# Control-plane records ride the ledger but are applied by CommandDispatcher /
# RunnerCommandSubscriber, never projected by DerivedView — no on_record arm by design.
_CONTROL_PLANE_RECORDS = frozenset({"CommandRecord", "CommandAckRecord"})


def test_every_cycle_record_is_dispatched_or_control_plane() -> None:
    """Every ``CycleRecord`` union member has an ``isinstance`` arm in
    ``DerivedView.on_record`` (or is an allowlisted control-plane record).

    The ``emit_*`` template's most error-prone step: add a ``*Record`` to the
    union, forget the dispatch arm, and it silently drops from every projection
    (dashboard, audit, SSE). Scanning ``on_record``'s source for its
    ``isinstance`` targets locks that a new record wires an arm + ``_handle_*``
    or is declared control-plane.
    """
    import typing

    from promptpotter.domain.run_records import CycleRecord

    union = typing.get_args(CycleRecord)[0]  # strip the Annotated[..., Field] wrapper
    members = {m.__name__ for m in typing.get_args(union)}

    on_record = next(
        n
        for n in ast.walk(parse(ROOT / "infrastructure" / "projections" / "base.py"))
        if isinstance(n, ast.FunctionDef) and n.name == "on_record"
    )
    dispatched: set[str] = set()
    for node in ast.walk(on_record):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and len(node.args) == 2
        ):
            targets = node.args[1].elts if isinstance(node.args[1], ast.Tuple) else [node.args[1]]
            dispatched |= {t.id for t in targets if isinstance(t, ast.Name)}

    unrouted = members - dispatched - _CONTROL_PLANE_RECORDS
    assert not unrouted, (
        "CycleRecord members with no DerivedView.on_record arm (silent-drop from "
        "every projection) — add an isinstance branch + _handle_*, or add to "
        f"_CONTROL_PLANE_RECORDS if applied by the dispatcher: {sorted(unrouted)}"
    )
    stale = _CONTROL_PLANE_RECORDS - members
    assert not stale, f"_CONTROL_PLANE_RECORDS names non-union records: {sorted(stale)}"


# The six charter category files — the locked suite shape.
_CATEGORY_FILES = frozenset(
    {
        "test_structure.py",
        "test_invariants.py",
        "test_numerics.py",
        "test_contracts.py",
        "test_resume.py",
        "test_shapes.py",
    }
)


def test_category_files_are_collectible() -> None:
    """The suite is exactly the six charter category files — no more, no less.

    Each carries the standard ``test_`` prefix, so pytest's default glob collects
    it with no ``python_files`` override; the support modules (``_scan``,
    ``_factories``, ``conftest``) are excluded by their underscore/`conftest`
    names. This fails loud on (a) a category file gone missing or (b) a stray
    ``test_*.py`` drifting in — coverage belongs in an existing category file,
    never a new one.
    """
    tests_dir = Path(__file__).parent
    on_disk = {
        p.name
        for p in tests_dir.glob("*.py")
        if not p.name.startswith("_") and p.name != "conftest.py"
    }
    assert on_disk == _CATEGORY_FILES, (
        f"suite shape drifted — missing: {sorted(_CATEGORY_FILES - on_disk)}, "
        f"stray (coverage belongs in a category file, not a new one): "
        f"{sorted(on_disk - _CATEGORY_FILES)}"
    )
