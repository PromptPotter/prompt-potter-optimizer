"""Champion election — promote a state, and coronate a challenger head-to-head.

The reigning champion is the meta-prompt state the shipped optimizer starts from. It is
recorded in ``datasets/_optimizer_meta/champion.json`` (pointer + its prompt_state +
evidence); the champion registry marks its row ``champion``.

A **coronation** is a paired head-to-head between a challenger and the reigning champion.
Because both rows carry a per-cell anchor-to-origin effect, the challenger−champion
difference per cell is just ``challenger.mean_d − champion.mean_d`` (origin cancels) — so
a coronation is computable from the registry alone, no new run, whenever they share cells.
The champion is dethroned only when the pooled CI clears zero (ties keep the incumbent —
stability = distributability).

**Graduation** (:func:`apply_champion_to_optimizer`) writes the reigning champion's
``prompt_state`` field values into ``datasets/_optimizer/pipeline.json`` — the distributable
optimizer every normal (non-L4) run loads. This is ONE mechanism doing two jobs, because
inner pp-self cycles load ``_optimizer/`` from disk: graduating (a) makes the champion the
shipped default every end-user's run benefits from, and (b) seeds the NEXT pp-self run's
inner *origin* from the champion, so the outer search compounds on the best-so-far instead
of restarting from stock each time. That's the "optimization" accumulation mode (the
"research/analysis" mode is :func:`reduce_corpus`, which only reads + ranks, never writes).
It edits prose fields only; a ``layout`` edit lives in ``NODE_LAYOUTS`` (code), so it is
reported as skipped rather than silently dropped.
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from promptpotter.application.meta_champion.reducer import (
    CandidateRow,
    champion_pointer_path,
    read_registry,
)
from promptpotter.shared.statistics import paired_diff_posterior


class ChampionPointer(BaseModel):
    """The reigning champion — the distributable pick."""

    model_config = ConfigDict(extra="forbid")

    state_hash: str
    label: str
    prompt_state: dict[str, dict[str, str]]
    anchor_effect: float
    n_cells: int
    since: str


class CoronationOutcome(BaseModel):
    """The paired verdict of a challenger vs the reigning champion."""

    model_config = ConfigDict(extra="forbid")

    challenger: str
    champion: str | None
    effect: float  # pooled (challenger − champion) across shared cells
    ci_lo: float
    ci_hi: float
    n_shared_cells: int
    decision: str  # crowned | held | insufficient
    detail: str


def read_champion_pointer() -> ChampionPointer | None:
    path = champion_pointer_path()
    if not path.is_file():
        return None
    try:
        return ChampionPointer.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_champion_pointer(pointer: ChampionPointer) -> Path:
    path = champion_pointer_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(pointer.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def _find_row(base_dir: Path, state_hash: str) -> CandidateRow | None:
    registry = read_registry(base_dir)
    if registry is None:
        return None
    return next((r for r in registry.candidates if r.state_hash == state_hash), None)


def promote_champion(base_dir: Path, state_hash: str) -> tuple[ChampionPointer | None, str]:
    """Elect ``state_hash`` as the reigning champion; write the pointer. Returns
    ``(pointer, message)`` — ``pointer`` is ``None`` when the state is unknown."""
    row = _find_row(base_dir, state_hash)
    if row is None:
        return None, (
            f"champion promote: state {state_hash!r} not in the registry. "
            "Run `champion refresh` first, or check the hash."
        )
    pointer = ChampionPointer(
        state_hash=row.state_hash,
        label=row.label,
        prompt_state=row.prompt_state,
        anchor_effect=row.anchor_effect,
        n_cells=row.n_cells,
        since=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    path = _write_champion_pointer(pointer)
    return (
        pointer,
        f"Champion is now {row.state_hash} (anchor {row.anchor_effect:+.4f}). Pointer: {path}",
    )


def coronate(
    base_dir: Path, challenger_hash: str, *, promote_on_win: bool = True
) -> CoronationOutcome:
    """Paired head-to-head of ``challenger_hash`` vs the reigning champion, from the
    registry's per-cell anchor effects. Crowns the challenger (writes the pointer) when
    the pooled CI clears zero and ``promote_on_win``."""
    challenger = _find_row(base_dir, challenger_hash)
    if challenger is None:
        return CoronationOutcome(
            challenger=challenger_hash,
            champion=None,
            effect=0.0,
            ci_lo=0.0,
            ci_hi=0.0,
            n_shared_cells=0,
            decision="insufficient",
            detail=f"challenger {challenger_hash!r} not in the registry",
        )
    pointer = read_champion_pointer()
    if pointer is None:
        # No incumbent — the first promotion is uncontested.
        if promote_on_win:
            promote_champion(base_dir, challenger_hash)
        return CoronationOutcome(
            challenger=challenger_hash,
            champion=None,
            effect=challenger.anchor_effect,
            ci_lo=challenger.ci_lo,
            ci_hi=challenger.ci_hi,
            n_shared_cells=challenger.n_cells,
            decision="crowned" if promote_on_win else "held",
            detail="no incumbent — challenger crowned uncontested",
        )
    champion = _find_row(base_dir, pointer.state_hash)
    ch_by_cell = {c.cell: c.mean_d for c in challenger.per_cell_effects}
    cm_by_cell = {c.cell: c.mean_d for c in (champion.per_cell_effects if champion else [])}
    shared = sorted(c for c in ch_by_cell if c in cm_by_cell)
    if not shared:
        return CoronationOutcome(
            challenger=challenger_hash,
            champion=pointer.state_hash,
            effect=0.0,
            ci_lo=0.0,
            ci_hi=0.0,
            n_shared_cells=0,
            decision="insufficient",
            detail=(
                "challenger and champion share no measured cells — run both on the "
                "canonical panel (`new promptpotter-self`) before coronating"
            ),
        )
    effect, se, n = paired_diff_posterior(
        [ch_by_cell[c] for c in shared], [cm_by_cell[c] for c in shared]
    )
    ci_lo, ci_hi = effect - 1.96 * se, effect + 1.96 * se
    if ci_lo > 0:
        decision = "crowned"
        detail = f"pooled (challenger - champion) {effect:+.4f} clears 0 over {n} cells"
        if promote_on_win:
            promote_champion(base_dir, challenger_hash)
    else:
        decision = "held"
        detail = (
            f"pooled {effect:+.4f} [{ci_lo:+.3f}, {ci_hi:+.3f}] does not clear 0 — incumbent holds"
        )
    return CoronationOutcome(
        challenger=challenger_hash,
        champion=pointer.state_hash,
        effect=effect,
        ci_lo=ci_lo,
        ci_hi=ci_hi,
        n_shared_cells=n,
        decision=decision,
        detail=detail,
    )


class FieldChange(BaseModel):
    """One graduated prompt field — the before/after the operator reviews in git."""

    model_config = ConfigDict(extra="forbid")

    node: str
    field: str
    before: str
    after: str


class GraduationOutcome(BaseModel):
    """The result of applying the reigning champion to the distributable optimizer."""

    model_config = ConfigDict(extra="forbid")

    champion: str | None
    applied: bool  # False on dry-run, no champion, or no net change
    changes: list[FieldChange]
    skipped: list[str]  # node/field entries not applied, with the reason
    path: str
    detail: str


def _resolved_key(family: str, version: object) -> str:
    """Join a node's ``prompt_family`` / ``prompt_version`` into the ``resolved_prompts``
    registry key — mirror of ``dispatch.llm_call.prompts._resolved_key``."""
    return f"{family}/{version}" if version is not None else str(family)


def apply_champion_to_optimizer(*, dry_run: bool = False) -> GraduationOutcome:
    """Graduate the reigning champion into the distributable ``datasets/_optimizer``.

    Writes each champion ``prompt_state`` field into its node's body in
    ``_optimizer/pipeline.json::resolved_prompts`` — so the shipped optimizer (and
    thereby the next pp-self run's inner origin) starts from the champion. Prose fields
    only; a non-``PromptTemplate`` field (e.g. a ``layout`` edit, which lives in
    ``NODE_LAYOUTS`` code) is reported in ``skipped``, never silently dropped. Reads +
    writes repo-global paths (the pointer + the optimizer manifest), so it needs no
    tenant identity. ``dry_run`` computes the diff without writing."""
    # Lazy: keeps meta_champion import-light and sidesteps the dispatch package.
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        OPTIMIZER_PIPELINE_PATH,
    )
    from promptpotter.domain.opt_search_point import PromptTemplate

    pointer = read_champion_pointer()
    path_str = str(OPTIMIZER_PIPELINE_PATH)
    if pointer is None:
        return GraduationOutcome(
            champion=None,
            applied=False,
            changes=[],
            skipped=[],
            path=path_str,
            detail="No reigning champion — run `champion coronate <hash>` or `promote` first.",
        )

    manifest = json.loads(OPTIMIZER_PIPELINE_PATH.read_text(encoding="utf-8"))
    nodes = manifest.get("nodes", {})
    resolved = manifest.get("resolved_prompts", {})
    prompt_fields = set(PromptTemplate.model_fields)

    changes: list[FieldChange] = []
    skipped: list[str] = []
    for node, fields in pointer.prompt_state.items():
        node_cfg = (nodes.get(node) or {}).get("config", {})
        family = node_cfg.get("prompt_family")
        if not family:
            skipped.append(f"{node} (no prompt_family in the _optimizer manifest)")
            continue
        body = resolved.get(_resolved_key(family, node_cfg.get("prompt_version")))
        if not isinstance(body, dict):
            skipped.append(f"{node} (resolved_prompts body missing)")
            continue
        for field, new_val in fields.items():
            if field not in prompt_fields:
                skipped.append(
                    f"{node}.{field} (not a prompt field — a layout edit needs a NODE_LAYOUTS change)"
                )
                continue
            before, after = str(body.get(field, "")), str(new_val)
            if before == after:
                continue
            changes.append(FieldChange(node=node, field=field, before=before, after=after))
            body[field] = after

    applied = bool(changes) and not dry_run
    if applied:
        tmp = OPTIMIZER_PIPELINE_PATH.with_suffix(".json.tmp")
        # ensure_ascii=True matches the manifest's dominant convention, so the diff
        # is the graduated fields (+ pre-existing whitespace normalization), not a
        # whole-file re-encode.
        tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        tmp.replace(OPTIMIZER_PIPELINE_PATH)
        _invalidate_optimizer_prompt_caches()

    if not changes:
        detail = (
            f"Champion {pointer.state_hash} already matches _optimizer "
            f"({len(skipped)} field(s) skipped)."
            if skipped
            else f"Champion {pointer.state_hash} already matches the distributable _optimizer — nothing to do."
        )
    else:
        verb = "Would graduate" if dry_run else "Graduated"
        detail = (
            f"{verb} champion {pointer.state_hash}: {len(changes)} field(s) "
            f"({', '.join(sorted({c.node for c in changes}))}) into {OPTIMIZER_PIPELINE_PATH.name}."
        )
    return GraduationOutcome(
        champion=pointer.state_hash,
        applied=applied,
        changes=changes,
        skipped=skipped,
        path=path_str,
        detail=detail,
    )


def _invalidate_optimizer_prompt_caches() -> None:
    """Drop the process-wide optimizer-manifest / prompt lru_caches after a write, so a
    long-lived process (e.g. the API) re-reads the graduated file rather than a stale copy.
    Harmless in the one-shot CLI (nothing cached yet)."""
    from promptpotter.application.optimization.dispatch.llm_call import prompts as _p

    for fn in (_p._load_optimizer_manifest, _p.get_optimizer_schema, _p.base_optimizer_template):
        with contextlib.suppress(AttributeError):
            fn.cache_clear()


__all__ = [
    "ChampionPointer",
    "CoronationOutcome",
    "FieldChange",
    "GraduationOutcome",
    "apply_champion_to_optimizer",
    "champion_pointer_path",
    "coronate",
    "promote_champion",
    "read_champion_pointer",
]
