"""``Campaign`` — one declared effort, holding a root cycle plus fork/diag descendants FLAT under ``cycles/``. Two
``new`` calls on an unchanged declaration share the root cycle id and origin score, then diverge from round 1."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import ConfigDict, Field

from promptpotter.domain.bench import DatasetSplit
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.pipeline_schema import stable_hash
from promptpotter.domain.strict_model import StrictModel


class Treatment(StrictModel):
    """Which optimizer ran, as it decides its behaviour: two runs are one treatment only where
    ``digest`` agrees. No measurement key reads it, so an arm two treatments propose replays free."""

    model_config = ConfigDict(frozen=True)

    optimizer: str
    version: str
    # Per llm node, what shapes its call — the values each round stamps and a resume diverges on.
    prompt_hashes: dict[str, str]
    # Per member node, its validated knobs: an edit is a new treatment, and a policy diff to resume.
    knobs: dict[str, dict[str, Any]]
    # The code deciding what its prompts say (`OptimizerRuntime.source_digest`).
    source: str

    @property
    def digest(self) -> str:
        return stable_hash(self.model_dump(mode="json"))


class Instrument(StrictModel):
    """What graded a bench headline: two are one quantity only where every field agrees."""

    model_config = ConfigDict(frozen=True)

    dataset_name: str
    # The bank's rows, order-independent: two banks re-cut under one name share ids, not content.
    dataset_hash: str | None
    split: DatasetSplit | None
    # A digest of the held-out ids `bank_partition.json` names — what the split and its seed drew.
    bench_rows: str
    # The run's grader, as `ScorerSetup.scorer_id` names it.
    scorer_id: str
    # node -> model at the origin: the target every selection's bench pass ran through.
    models: dict[str, str]


def bench_instrument(
    *,
    dataset_name: str,
    dataset_hash: str | None,
    split: DatasetSplit | None,
    bench_ids: Iterable[int],
    scorer_id: str,
    models: Mapping[str, str],
) -> Instrument:
    held_out = ",".join(str(i) for i in sorted(bench_ids))
    return Instrument(
        dataset_name=dataset_name,
        dataset_hash=dataset_hash,
        split=split,
        bench_rows=hashlib.sha256(held_out.encode()).hexdigest()[:12],
        scorer_id=scorer_id,
        models=dict(models),
    )


class Campaign(StrictModel):
    """Frozen manifest — identity, config and operator VISIBILITY INTENT only, never run state: that is per-cycle,
    and a stored campaign status was overwritten by whichever cycle finalized last."""

    model_config = ConfigDict(frozen=True)

    campaign_id: str
    dataset_name: str
    label: str = ""
    created_at: str
    root_cycle_id: str
    root_content_hash: str = ""
    # `None` only on an unstarted check-in, which has not chosen what it runs.
    treatment: Treatment | None = None
    backend_id: str = ""
    # Connector KIND, FROZEN at mint: a campaign OUTLIVES its dataset dir, so re-pointing a slug
    # must not re-kind a campaign that already measured under the old one.
    backend_type: str = ""
    owner_user_id: str = "default"
    # VISIBILITY only — the authoring phase is NOT here. `.runtime/checkin.flag` on the root cycle
    # owns it (`runtime_flags.py::is_checkin`), which is also what `derive_run_phase` serves.
    lifecycle_status: Literal["active", "archived", "deleted"] = "active"
    lifecycle_changed_at: str = ""
    lifecycle_reason: str = ""
    config: dict[str, Any] = Field(default_factory=dict)

    @property
    def root_hop(self) -> CycleHop:
        """This campaign's root cycle as the pair that addresses it. Re-pairing at a call site risks one campaign's id with
        another's root cycle — easy, because a content-addressed ``root_cycle_id`` is shared by siblings."""
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.root_cycle_id)


__all__ = ["Campaign", "Instrument", "Treatment", "bench_instrument"]
