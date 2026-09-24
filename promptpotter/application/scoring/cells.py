"""The measurement log and one cell opened — the two cell reads `GET /datasets/{name}/cells` and
`GET /datasets/{name}/cells/{run_id}/{sample_id}` serve.

`measurement_log` resolves a scope (cycle / campaign / dataset), pages it and RANKS it: which rows
a page holds and in what order are decided here, once, so every entry point reads the same log.
Scores are served, never recomputed — the Rasch artifact is read off disk and paged; the ordering
it carries is the backend's answer (`webapp/CLAUDE.md` § Scoring authority).

`open_cell` assembles an archive row into its trace. The spans are READ-TIME assembly over what
the row already banked, never a second record of it. A node's input is re-rendered from the run's
own node config over the sample fields a row keeps (`query`, `ground_truth`, `question`), so a
template reading any other `Sample` field renders it blank; outputs are attributed through the
dataset's CURRENT pipeline schema."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    dataset_cell_scorer,
    load_dataset_campaign_config,
)
from promptpotter.application.datasets.loaders import samples_from_dicts
from promptpotter.application.intelligence.adaptive_queue_mechanism import marginal_hit_probability
from promptpotter.application.intelligence.hard_sample_archive import (
    build_archive_hard_samples_artifact,
)
from promptpotter.application.scoring.sample_measurement import interpolate_prompt
from promptpotter.domain.cells import (
    Cell,
    CellCandidate,
    CellRow,
    CellSpan,
    CellsResponse,
    DatasetItem,
    HeatmapScope,
)
from promptpotter.domain.cycle_paths import CycleHop, CyclePath
from promptpotter.domain.dashboard_rows import SampleStatus, sample_status
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.results import HardSampleOrder
from promptpotter.domain.scoring import is_hit, recorded_cost_s
from promptpotter.infrastructure.store.archive_queries import load_run, read_cold_payload
from promptpotter.infrastructure.store.cell_queries import (
    ScopeCells,
    campaign_cells,
    cycle_cells,
    dataset_cells,
)
from promptpotter.infrastructure.store.dataset_access import (
    dataset_panel_rows,
    dataset_pipeline_path,
    readable_dataset_dir,
    readable_dataset_rows,
)
from promptpotter.infrastructure.store.io import read_json, read_yaml
from promptpotter.infrastructure.store.layout import campaign_root_dir_for, cycle_dir_for
from promptpotter.infrastructure.store.stores import Stores, resolve_cycle_path
from promptpotter.shared.errors import BadRequestError, NotFoundError, PayloadInvalidError

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema

__all__ = ["assemble_cell", "measured_row", "measurement_log", "open_cell"]

# Folded into the spans, so not repeated beside them.
_SPAN_KEYS = frozenset({"step_tokens", "step_timings", "terminal_node", "total_time"})


def measured_row(
    detail: dict[str, Any], sample_id: int, cold: list[dict[str, Any]] | None
) -> dict[str, Any] | None:
    """The run's row for *sample_id* — the last one, as the archive folds — with what
    `compact-archive` moved to the cold store put back. The cold entry only FILLS absent keys: it
    is aligned by line index, and a row appended after compaction must not be overwritten by it."""
    row = next(
        (
            r
            for r in reversed(detail.get("measurements") or [])
            if isinstance(r, dict) and r.get("sample_id") == sample_id
        ),
        None,
    )
    if row is None:
        return None
    entries = [e for e in cold or [] if e.get("k") == f"m:{sample_id}"]
    if not entries:
        return row
    entry = max(entries, key=lambda e: e.get("i", -1))
    pd = row.get("pipeline_data")
    return {
        **(entry.get("row") or {}),
        **row,
        "pipeline_data": {**(entry.get("pd") or {}), **(pd if isinstance(pd, dict) else {})},
    }


def _span(
    node: str,
    cfg: dict[str, Any],
    pd: dict[str, Any],
    variables: dict[str, Any],
    output_keys: list[str],
) -> CellSpan:
    tokens = (pd.get("step_tokens") or {}).get(node)
    tokens = tokens if isinstance(tokens, dict) else {}
    seconds = (pd.get("step_timings") or {}).get(node)
    prompt = cfg.get("prompt")
    return CellSpan(
        node=node,
        model=tokens.get("model") or cfg.get("model"),
        provider=tokens.get("provider") or cfg.get("provider"),
        input=interpolate_prompt(prompt, variables) if isinstance(prompt, str) else None,
        config={k: v for k, v in cfg.items() if k != "prompt"},
        outputs={k: pd[k] for k in output_keys if k in pd},
        seconds=float(seconds) if isinstance(seconds, int | float) else None,
        input_tokens=tokens.get("input"),
        output_tokens=tokens.get("output"),
        cache_read_tokens=tokens.get("cache_read"),
        cost_usd=tokens.get("cost_usd"),
        estimated=bool(tokens.get("estimated", False)),
    )


def assemble_cell(
    detail: dict[str, Any], row: dict[str, Any], schema: PipelineSchema | None, *, run_id: str
) -> Cell:
    """*row* as its trace. *schema* attributes outputs to nodes; without one every output stays
    in ``other_outputs`` rather than being guessed onto a node."""
    pd = row.get("pipeline_data")
    pd = pd if isinstance(pd, dict) else {}
    sid = int(row["sample_id"])
    variables = {
        "id": sid,
        "query": row.get("query") or "",
        "ground_truth": row.get("ground_truth") or None,
        "question": pd.get("question"),
    }
    outputs_of = {n.name: n.output_keys for n in schema.nodes} if schema is not None else {}
    spans = [
        _span(
            str(node), cfg if isinstance(cfg, dict) else {}, pd, variables, outputs_of.get(node, [])
        )
        for node, cfg in detail.get("node_configs") or []
    ]
    attributed = {k for s in spans for k in s.outputs} | _SPAN_KEYS
    fitness = row.get("fitness")
    return Cell(
        run_id=run_id,
        sample_id=sid,
        dataset_name=detail.get("dataset_name"),
        run_name=str(detail.get("name") or ""),
        created_at=detail.get("created_at"),
        prompt_fields_id=detail.get("prompt_fields_id"),
        query=str(row.get("query") or ""),
        ground_truth=str(row.get("ground_truth") or ""),
        predicted=str(row.get("predicted") or ""),
        status=sample_status(row),
        fitness=float(fitness) if isinstance(fitness, int | float) else None,
        cached=bool(row.get("cached", False)),
        error=row.get("error"),
        terminal_node=pd.get("terminal_node"),
        seconds=recorded_cost_s(row),  # type: ignore[arg-type]
        spans=spans,
        other_outputs={k: v for k, v in pd.items() if k not in attributed},
    )


def open_cell(stores: Stores, name: str, run_id: str, sample_id: int) -> Cell:
    """The cell at ``(run_id, sample_id)``, which must be a run filed under dataset *name*."""
    # The first read keyed on a URL-supplied run id — it becomes a file name under the archive.
    if not run_id or any(ch in run_id for ch in "/\\") or ".." in run_id:
        raise NotFoundError(f"Run '{run_id}' not found")
    dataset_dir = readable_dataset_dir(stores, name)
    detail = load_run(stores, run_id)
    if detail is None or detail.get("dataset_name") != name:
        raise NotFoundError(f"Run '{run_id}' not found under dataset '{name}'")
    row = measured_row(detail, sample_id, read_cold_payload(stores, run_id))
    if row is None:
        raise NotFoundError(f"Run '{run_id}' holds no cell for sample {sample_id}")
    pipeline = dataset_pipeline_path(dataset_dir)
    schema = parse_pipeline_response(read_yaml(pipeline)) if pipeline.is_file() else None
    return assemble_cell(detail, row, schema, run_id=run_id)


def _load_dataset_rows(
    stores: Stores, name: str
) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """Resolve *name*'s rows, normalising the sample-id key at the read boundary. Missing rows are
    not an unknown dataset but a bank-less one, so this answers an honest empty 200, not a 404.

    Connector-owned panels FIRST, through the gateway's own pair: a harbor dataset or an L4 inner
    benchmark declares its bank in an ``experiment_file`` and materializes no rows, so the
    materialized half alone answers an empty roster for exactly those campaigns."""
    raw: dict[str, Any] | None
    try:
        panel = dataset_panel_rows(stores, name)
    except (ValueError, OSError, ImportError) as exc:
        # Never an empty roster: "this panel could not be read" and "this dataset has no bank" are
        # different facts, and the browser already spells them differently.
        raise PayloadInvalidError(
            f"Dataset {name!r} declares a connector-owned panel that could not be read: {exc}",
            code="dataset_panel_invalid",
            details={"dataset_name": name},
        ) from exc
    if panel is not None:
        raw = {"name": name, "items": [s.model_dump() for s in samples_from_dicts(panel[0])]}
    else:
        raw = readable_dataset_rows(stores, name)
    if raw is None:
        return {"name": name, "items": []}, {}
    sample_lookup: dict[int, dict[str, Any]] = {}
    for item in raw["items"]:
        sid = int(item["sample_id"] if "sample_id" in item else item["id"])
        sample_lookup[sid] = item
    return raw, sample_lookup


def _artifact_scope_store(
    stores: Stores,
    campaign_id: str | None,
    cycle_id: str | None,
    descend: CyclePath,
) -> tuple[Stores, str | None, str | None]:
    """Store + ids the scope artifact lives in. With ``?descend=`` the viewed leaf is an L4 inner
    cycle in an off-registry sandbox, so the walk starts at the ROOT hop and both ids are required."""
    if not descend:
        return stores, campaign_id, cycle_id
    if not campaign_id or not cycle_id:
        raise BadRequestError("descend requires campaign_id and cycle_id (the root hop)")
    leaf_store, leaf = resolve_cycle_path(
        stores, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *descend)
    )
    return leaf_store, leaf.campaign_id, leaf.cycle_id


def _resolve_scope_artifact(
    stores: Stores,
    *,
    scope: HeatmapScope,
    name: str,
    campaign_id: str | None,
    cycle_id: str | None,
) -> dict[str, Any]:
    """Resolve the hard-samples artifact for *scope*. Missing `hard_samples.json` returns `{}`
    (heatmap renders empty); missing campaign/cycle DIR is a real 404.
    """

    if scope == "cycle":
        if not campaign_id or not cycle_id:
            raise BadRequestError("scope=cycle requires campaign_id and cycle_id")
        cycle_dir = cycle_dir_for(
            stores.base_dir, CycleHop(campaign_id=campaign_id, cycle_id=cycle_id)
        )
        if not cycle_dir.exists():
            raise NotFoundError(f"Cycle '{campaign_id}/{cycle_id}' not found")
        path = cycle_dir / "hard_samples.json"
        if not path.is_file():
            return {}
        cycle_artifact: dict[str, Any] = read_json(path)
        return cycle_artifact
    if scope == "campaign":
        if not campaign_id:
            raise BadRequestError("scope=campaign requires campaign_id")
        campaign_dir = campaign_root_dir_for(stores.base_dir, campaign_id)
        if not campaign_dir.exists():
            raise NotFoundError(f"Campaign '{campaign_id}' not found")
        path = campaign_dir / "hard_samples.json"
        if not path.is_file():
            return {}
        campaign_artifact: dict[str, Any] = read_json(path)
        return campaign_artifact
    # `dataset` — always per-dataset (cross-dataset pooling is meaningless), so the grade is the
    # one that dataset declares; there is no campaign in scope to ask.

    scorer, scorer_id = dataset_cell_scorer(readable_dataset_dir(stores, name))
    return build_archive_hard_samples_artifact(
        stores,
        dataset_name=name,
        scorer=scorer,
        scorer_id=scorer_id,
        top_k_samples=None,
    )


def _trim_unmeasured(
    order: list[int],
    measured: set[int],
    cap: int | None,
) -> list[int]:
    """Keep all measured + at most *cap* unmeasured (drops the tail of the unmeasured block, not
    a random slice). `cap=0` ⇒ measured only; `cap=None` ⇒ no-op.
    """
    if cap is None:
        return list(order)
    kept_unmeasured = 0
    out: list[int] = []
    for sid in order:
        if sid in measured:
            out.append(sid)
        elif kept_unmeasured < cap:
            out.append(sid)
            kept_unmeasured += 1
    return out


@dataclass(frozen=True)
class _LeaderboardPage:
    """One page of the hard-sample leaderboard, resolved ONCE.

    The rank lives here because it needs both halves — the Rasch maps AND the per-sample
    series — which is why the ordering had drifted into the browser, the one place they meet.
    """

    raw: dict[str, Any]
    sample_lookup: dict[int, dict[str, Any]]
    artifact: dict[str, Any]
    delta_map: dict[int, float]
    delta_se_map: dict[int, float]
    n_obs_map: dict[int, int]
    pick_score_map: dict[int, float]
    candidates: list[CellCandidate]
    cells: list[CellRow]
    order: HardSampleOrder
    ranked: list[int]
    """Page sample_ids in served order; index + 1 IS each row's ``hard_sample_rank``."""


def _resolve_leaderboard_page(
    stores: Stores,
    *,
    name: str,
    scope: HeatmapScope,
    campaign_id: str | None,
    cycle_id: str | None,
    descend: CyclePath,
    limit: int,
    max_unmeasured: int | None,
    order: HardSampleOrder | None,
) -> _LeaderboardPage:
    """Resolve the page, then rank it — the single owner of hard-sample order.

    Selection (δ_s-desc, unmeasured trimmed, then ``limit``) picks WHICH rows the page holds;
    the rank picks the order they are read in. Deliberately different keys: selection cannot
    depend on the cells, which are only read for rows already selected.

    *Measured* is a GRADED cell in THIS scope, not a δ entry — the ruler persists across rounds
    and inherits from parent fits, so a sample carries a δ it never earned here.
    """
    raw, sample_lookup = _load_dataset_rows(stores, name)
    art_store, art_campaign, art_cycle = _artifact_scope_store(
        stores, campaign_id, cycle_id, descend
    )
    artifact = _resolve_scope_artifact(
        art_store, scope=scope, name=name, campaign_id=art_campaign, cycle_id=art_cycle
    )
    rasch = artifact.get("rasch", {})
    delta_map: dict[int, float] = {int(k): float(v) for k, v in rasch.get("delta", {}).items()}
    delta_se_map: dict[int, float] = {
        int(k): float(v) for k, v in rasch.get("delta_se", {}).items()
    }
    n_obs_map: dict[int, int] = {
        int(k): int(v) for k, v in rasch.get("n_obs_per_sample", {}).items()
    }
    pick_score_block = artifact.get("pick_score", {}).get("per_sample", {})
    pick_score_map: dict[int, float] = {int(k): float(v) for k, v in pick_score_block.items()}

    fitted = {sid for sid in delta_map if sid in sample_lookup}
    selection = sorted(sample_lookup.keys(), key=lambda s: (-delta_map.get(s, 0.0), s))
    selected = _trim_unmeasured(selection, fitted, max_unmeasured)[:limit]

    candidates, cells = _page_cells(
        art_store, scope=scope, name=name, campaign=art_campaign, cycle=art_cycle, page=selected
    )

    resolved = order or _dataset_hard_sample_order(stores, name)
    key_map = pick_score_map if resolved == "info_gain" else delta_map
    measured = {c.sample_id for c in cells if _is_graded(c)}
    # Unmeasured rows carry a prior-fitted key they never earned in this scope, so they trail
    # on sample_id rather than sorting into the measured block on it.
    ranked = sorted(
        selected,
        key=lambda sid: (0, -key_map.get(sid, 0.0), sid) if sid in measured else (1, 0.0, sid),
    )
    return _LeaderboardPage(
        raw=raw,
        sample_lookup=sample_lookup,
        artifact=artifact,
        delta_map=delta_map,
        delta_se_map=delta_se_map,
        n_obs_map=n_obs_map,
        pick_score_map=pick_score_map,
        candidates=candidates,
        cells=cells,
        order=resolved,
        ranked=ranked,
    )


def _page_cells(
    art_store: Stores,
    *,
    scope: HeatmapScope,
    name: str,
    campaign: str | None,
    cycle: str | None,
    page: list[int],
) -> ScopeCells:
    """Three scopes, three sources, one cell shape — each walk emits `CellRow`s itself, so
    nothing is re-mapped here."""
    if scope == "cycle":
        assert campaign is not None and cycle is not None  # checked in resolver
        return cycle_cells(art_store, CycleHop(campaign_id=campaign, cycle_id=cycle), set(page))
    if scope == "campaign":
        assert campaign is not None  # checked in resolver
        return campaign_cells(art_store, campaign, set(page))
    return dataset_cells(art_store, dataset_name=name, wanted=set(page))


def _dataset_hard_sample_order(stores: Stores, name: str) -> HardSampleOrder:
    """The campaign default for this dataset, read off the same authored `campaign.yaml` the
    `dataset_split` footer reads. Absent file ⇒ the field default."""
    campaign_path = dataset_campaign_path(readable_dataset_dir(stores, name))
    if not campaign_path.is_file():
        return "info_gain"
    return load_dataset_campaign_config(campaign_path).hard_sample_order


def _is_graded(c: CellRow) -> bool:
    return c.fitness is not None and c.status != "ERR"


def _graded(cells: list[CellRow]) -> list[float]:
    return [c.fitness for c in cells if c.fitness is not None and _is_graded(c)]


def measurement_log(
    stores: Stores,
    name: str,
    *,
    scope: HeatmapScope,
    campaign_id: str | None,
    cycle_id: str | None,
    descend: CyclePath,
    limit: int,
    max_unmeasured: int | None,
    order: HardSampleOrder | None,
    candidate_id: str | None,
    round: int | None,
    status: SampleStatus | None,
) -> CellsResponse:
    """The measurement log of one scope — `GET /datasets/{name}/cells`. Under a filter,
    ``samples`` and ``candidates`` shrink to the ones holding a kept cell, so a preset (one
    candidate, one round) serves exactly its own rows. *descend* is the decoded L4 tail below
    the root hop ``(campaign_id, cycle_id)``; empty reads the root store."""
    dataset_dir = readable_dataset_dir(stores, name)
    page = _resolve_leaderboard_page(
        stores,
        name=name,
        scope=scope,
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        descend=descend,
        limit=limit,
        max_unmeasured=max_unmeasured,
        order=order,
    )
    sample_lookup = page.sample_lookup

    # Seed-centred marginal hit prob — see `adaptive_queue_mechanism.marginal_hit_probability`.
    rasch = page.artifact.get("rasch", {})
    sigma_theta = float(rasch.get("sigma_theta", 0.0))
    theta_map: dict[str, float] = {str(k): float(v) for k, v in rasch.get("theta", {}).items()}
    candidate_order_raw = page.artifact.get("candidate_order") or []
    seed_theta = (
        theta_map.get(str(candidate_order_raw[0]), 0.0)
        if candidate_order_raw and theta_map
        else 0.0
    )
    var_c = sigma_theta**2  # brand-new candidate's ability prior variance

    def _p_hat(sid: int) -> float | None:
        if sid not in page.delta_map:
            return None
        return marginal_hit_probability(
            mu_c=seed_theta,
            var_c=var_c,
            delta_s=page.delta_map[sid],
            se_delta_s=page.delta_se_map.get(sid, 0.0),
        )

    round_of = {c.key: c.round for c in page.candidates}
    individual_of = {c.key: c.candidate_id for c in page.candidates}
    kept = [
        c
        for c in page.cells
        if (candidate_id is None or individual_of.get(c.candidate) == candidate_id)
        and (round is None or round_of.get(c.candidate) == round)
        and (status is None or c.status == status)
    ]
    rank_of = {sid: i for i, sid in enumerate(page.ranked)}
    cand_pos = {c.key: i for i, c in enumerate(page.candidates)}
    # CHRONOLOGICAL: candidates in the order they ran, each one's cells in its walk order — so the
    # flat log is a time series, and a sample group (bucketed under the ranked `samples`) lists
    # its cells oldest first. A stable sort, so the walk order inside a candidate survives.
    kept.sort(key=lambda c: cand_pos[c.candidate])
    by_sample: dict[int, list[CellRow]] = {}
    for c in kept:
        by_sample.setdefault(c.sample_id, []).append(c)

    filtered = candidate_id is not None or round is not None or status is not None
    ranked = [sid for sid in page.ranked if sid in by_sample] if filtered else page.ranked
    held = {c.candidate for c in kept}
    candidates = [c for c in page.candidates if c.key in held] if filtered else page.candidates

    def _item(sid: int) -> DatasetItem:
        graded = _graded(by_sample.get(sid, []))
        return DatasetItem(
            sample_id=sid,
            query=sample_lookup[sid]["query"],
            ground_truth=sample_lookup[sid].get("ground_truth"),
            task=sample_lookup[sid].get("task"),
            # The position in the UNFILTERED ranking — a filter narrows the rows, it does not
            # re-rank them.
            hard_sample_rank=rank_of[sid] + 1,
            n_obs=page.n_obs_map.get(sid),
            delta=page.delta_map.get(sid),
            delta_se=page.delta_se_map.get(sid),
            p_hat=_p_hat(sid),
            pick_score=page.pick_score_map.get(sid),
            n_measured=len(graded),
            n_hits=sum(1 for f in graded if is_hit(f)),
            mean_fitness=sum(graded) / len(graded) if graded else None,
        )

    # Held-out test fold from campaign config — display-only; never materialized. Read off
    # the typed knob (`CampaignConfig.dataset_split`), not a raw-dict re-parse.
    campaign_path = dataset_campaign_path(dataset_dir)
    declared = (
        load_dataset_campaign_config(campaign_path).dataset_split
        if campaign_path.is_file()
        else None
    )
    graded_all = _graded(kept)
    return CellsResponse(
        name=page.raw["name"],
        scope=scope,
        row_count=len(sample_lookup),
        split_test=declared.test if declared else None,
        order=page.order,
        samples=[_item(sid) for sid in ranked],
        candidates=candidates,
        cells=kept,
        total_measurements=len(graded_all),
        total_hits=sum(1 for f in graded_all if is_hit(f)),
        mean_fitness=sum(graded_all) / len(graded_all) if graded_all else None,
    )
