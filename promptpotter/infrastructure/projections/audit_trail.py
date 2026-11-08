from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from promptpotter.domain.phases import STOP_REASON_INFO, RunPhase
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
    RunPhaseRecord,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_cycle_facts,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import LedgerIndex, LedgerSpan, RecordClasses

_STOPS = frozenset({RunPhase.TERMINAL, RunPhase.PAUSED})

type _Record = (
    LLMCallRecord | RoundClosedRecord | RoundEnteredRecord | RoundWarningRecord | RunPhaseRecord
)


def build_node_block(record: LLMCallRecord) -> NodeBlock:
    payload = record.payload
    usage = record.usage
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
        timestamp=record.timestamp,
        config=payload.get("config") or {},
        model=payload.get("model"),
        usage=usage,
        # A replay's counts are the banked call's: a share of them is a discount this run never got.
        prefix=None if usage is None else usage.prefix(replayed=bool(payload.get("cached"))),
        duration_s=payload.get("duration_s"),
        finish_reason=payload.get("finish_reason") or None,
        schema_repair_errors=payload.get("schema_repair_errors") or [],
    )


class RoundAudits:
    """Each round's audit, folded off the cycle's OWN ledger: nothing is carried across a fork's cut.
    A round answers once its close lands, an open one once the run's stop lands (``interrupted``);
    a call landing after a close merges into the round just closed."""

    records: ClassVar[RecordClasses] = (
        LLMCallRecord,
        RoundClosedRecord,
        RoundEnteredRecord,
        RoundWarningRecord,
        RunPhaseRecord,
    )

    def __init__(self) -> None:
        self._audits: dict[int, RoundAudit] = {}
        self._round = 0
        self._closed = False
        self._nodes: dict[str, NodeBlock] = {}
        self._warnings: list[LoopWarning] = []
        self._started_at: str | None = None
        self._finished_at: str | None = None

    def feed(self, record: _Record) -> None:
        if isinstance(record, RoundEnteredRecord):
            self._settle(interrupted=False)
            self._round, self._closed = record.round, False
            self._started_at, self._finished_at = record.timestamp, None
            if record.rewound:
                # A displaced round's audit is no round the cycle ran.
                self._audits = {n: a for n, a in self._audits.items() if n < record.round}
            return
        if isinstance(record, RoundClosedRecord):
            self._closed, self._finished_at = True, record.timestamp
            self._settle(interrupted=False)
            return
        if isinstance(record, RunPhaseRecord):
            if record.run_phase in _STOPS and record.stop_reason is not None:
                cuts = STOP_REASON_INFO[record.stop_reason].halts_mid_round
                self._settle(interrupted=cuts and not self._closed)
            return
        if isinstance(record, LLMCallRecord):
            self._nodes[record.node] = build_node_block(record)
        else:
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
        if self._closed:
            self._settle(interrupted=False)

    def _settle(self, *, interrupted: bool) -> None:
        if not self._nodes and not self._warnings:
            return
        prior = self._audits.get(self._round)
        self._audits[self._round] = RoundAudit(
            round=self._round,
            started_at=(prior.started_at if prior else None) or self._started_at,
            finished_at=self._finished_at,
            nodes={**(prior.nodes if prior else {}), **self._nodes},
            warnings=[*(prior.warnings if prior else []), *self._warnings],
            interrupted=interrupted,
        )
        self._nodes, self._warnings = {}, []

    def value(self) -> dict[int, RoundAudit]:
        return dict(self._audits)


def _ran_by(cycle_dir: Path, round_num: int) -> Path:
    """The ledger of the cycle that RAN *round_num*: a fork's rounds below its cut are its parent's."""
    while True:
        ledger = CycleLayout(cycle_dir).ledger
        minted = scan_cycle_facts(ledger).minted
        entered = scan_standing_rounds([LedgerSpan(ledger)]).entered
        parent = None if minted is None else minted.parent_cycle_dir(cycle_dir)
        if parent is None or (entered is not None and round_num >= entered):
            return ledger
        cycle_dir = parent


def round_audits(cycle_dir: Path, round_nums: list[int]) -> list[RoundAudit | None]:
    return [LedgerIndex.of(_ran_by(cycle_dir, n), ()).view(RoundAudits).get(n) for n in round_nums]
