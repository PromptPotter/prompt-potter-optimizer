from __future__ import annotations

import logging
from pathlib import Path

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.round_audit import (
    LoopWarning,
    NodeBlock,
    NodeInput,
    NodeOutput,
    RoundAudit,
)
from promptpotter.domain.run_records import (
    LLMCallRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
    RoundWarningRecord,
)
from promptpotter.domain.spend import TokenAccount
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_cycle_facts,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.io import read_json_tolerant, unlink_robust, write_json
from promptpotter.infrastructure.store.layout import (
    ROUND_GLOB,
    CycleLayout,
    round_basename,
    round_number,
)
from promptpotter.infrastructure.store.read_model import LedgerSpan

logger = logging.getLogger(__name__)

__all__ = [
    "AuditTrailProjection",
    "build_node_block",
    "load_round_audits",
]


_ROUNDS_SUBPATH = (".runtime", "cache", "rounds")


def _audit_round_file(cycle_dir: Path, round_num: int) -> Path:
    """A round's audit lives on the cycle that RAN it: nothing is carried across a fork's cut."""
    while True:
        layout = CycleLayout(cycle_dir)
        minted = scan_cycle_facts(layout.ledger).minted
        entered = scan_standing_rounds([LedgerSpan(layout.ledger)]).entered
        if (
            minted is None
            or minted.parent_cycle_id is None
            or (entered is not None and round_num >= entered)
        ):
            return layout.audit_round_file(round_num)
        cycle_dir = cycle_dir.parent / minted.parent_cycle_id


def _read_audit(path: Path) -> RoundAudit | None:
    document = read_json_tolerant(path)
    return None if document is None else RoundAudit.model_validate(document)


def load_round_audits(cycle_dir: Path, round_nums: list[int]) -> list[RoundAudit | None]:
    return [_read_audit(_audit_round_file(cycle_dir, n)) for n in round_nums]


def build_node_block(record: LLMCallRecord) -> NodeBlock:
    payload = record.payload
    if record.payload_kind == "synthesized":
        return NodeBlock(
            input=NodeInput(),
            output=NodeOutput(response=payload.get("response"), reasoning=None),
            synthesized=True,
            timestamp=record.timestamp,
            config={},
            model=None,
            usage=None,
            prefix=None,
            duration_s=None,
            finish_reason=None,
            schema_repair_errors=[],
        )
    usage = TokenAccount.from_payload(payload.get("usage"))
    rendered = "template_name" in payload
    return NodeBlock(
        input=NodeInput(
            template_name=payload.get("template_name"),
            template_fields=payload.get("template_fields") or {},
            variables=payload.get("variables") or {},
            messages=[] if rendered else payload.get("messages") or [],
        ),
        output=NodeOutput(
            response=payload.get("response"), reasoning=payload.get("reasoning") or None
        ),
        synthesized=False,
        timestamp=record.timestamp,
        config=payload.get("config") or {},
        model=payload.get("model"),
        usage=usage,
        # A replay's counts are the banked call's: a share of them is a discount this run never got.
        prefix=usage.prefix(replayed=bool(payload.get("cached"))),
        duration_s=payload.get("duration_s"),
        finish_reason=payload.get("finish_reason") or None,
        schema_repair_errors=payload.get("schema_repair_errors") or [],
    )


class AuditTrailProjection(Projection):
    def __init__(self, rounds_dir: Path) -> None:
        if rounds_dir.parts[-len(_ROUNDS_SUBPATH) :] != _ROUNDS_SUBPATH:
            raise ValueError(
                f"AuditTrailProjection rounds_dir must end in {'/'.join(_ROUNDS_SUBPATH)}; "
                f"got {rounds_dir}"
            )
        self.rounds_dir = rounds_dir
        self._current_round: int = 0
        self._nodes: dict[str, NodeBlock] = {}
        self._warnings: list[LoopWarning] = []
        self._started_at: str | None = None
        self._finished_at: str | None = None

    @classmethod
    def from_cycle_dir(cls, cycle_dir: CycleDir) -> AuditTrailProjection:
        return cls(CycleLayout(Path(cycle_dir)).audit_rounds)

    def begin_round(self, round_num: int, started_at: str) -> None:
        """Flushes first: L2/L3 calls arriving after a close merge into the just-closed round."""
        if self._nodes or self._warnings:
            self.flush()
        self._current_round = round_num
        self._nodes = {}
        self._warnings = []
        self._started_at = started_at
        self._finished_at = None

    # Origin IS round 0: `emit_origin_round` closes it through the same `close_round` seam.
    def _handle_round_entered(self, record: RoundEnteredRecord) -> None:
        self.begin_round(record.round, started_at=record.timestamp)
        if record.rewound and self.rounds_dir.exists():
            # A displaced round's audit left behind is read back as a round the cycle ran.
            for path in sorted(self.rounds_dir.glob(ROUND_GLOB)):
                if (n := round_number(path)) is not None and n >= record.round:
                    unlink_robust(path)

    def _handle_round_closed(self, record: RoundClosedRecord) -> None:
        self._finished_at = record.timestamp
        self.flush()

    def _handle_llm_call(self, record: LLMCallRecord) -> None:
        self._nodes[record.node] = build_node_block(record)

    def _handle_round_warning(self, record: RoundWarningRecord) -> None:
        self._warnings.append(
            LoopWarning(
                ts=record.timestamp,
                kind=record.kind,
                severity=record.severity,
                message=record.message,
                round=record.round,
                detail=dict(record.detail),
            )
        )

    def flush(self, *, interrupted: bool = False) -> Path | None:
        """A second flush MERGES into the existing file, so late L2/L3 records are not lost."""
        if not self._nodes and not self._warnings:
            return None

        self.rounds_dir.mkdir(parents=True, exist_ok=True)
        path = self.rounds_dir / round_basename(self._current_round)

        prior = _read_audit(path)
        audit = RoundAudit(
            round=self._current_round,
            started_at=(prior.started_at if prior else None) or self._started_at,
            finished_at=self._finished_at,
            nodes={**(prior.nodes if prior else {}), **self._nodes},
            warnings=[*(prior.warnings if prior else []), *self._warnings],
            interrupted=interrupted,
        )
        write_json(path, audit.model_dump(), default=str)
        logger.debug(
            "Round %d recorded: %d nodes → %s",
            self._current_round,
            len(audit.nodes),
            path.name,
        )

        self._nodes = {}
        self._warnings = []
        return path

    def drain(self, *, interrupted: bool = False) -> None:
        """The runner's teardown seam: a mid-candidate interrupt never closes its round."""
        if self._nodes or self._warnings:
            self.flush(interrupted=interrupted)
