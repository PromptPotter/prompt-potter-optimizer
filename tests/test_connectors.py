"""The built-in connectors' own faults: what a backend's quirk or a host's state costs a cell."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from factories import backend_client, jumping_clock, pipeline_schema, samples, spend_book
from offline_world import Router

from promptpotter.application.scoring.cell_envelope import CellEnvelope
from promptpotter.application.scoring.sample_measurement import cell_billing
from promptpotter.connectors import dspy_module
from promptpotter.connectors.protocol import InProcessWorkload
from promptpotter.connectors.termnorm import TermNormSession
from promptpotter.domain.pipeline_params import NodeCall, PipelineParams
from promptpotter.domain.run_records import TokenUsageRecord
from promptpotter.domain.sample import Sample
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm import pricing
from promptpotter.infrastructure.llm.spend_book import SendBound
from promptpotter.shared.run_scope import scoped


async def _no_wait(*_: Any) -> None:
    return None


def _lm_bills(ledger: Path) -> list[TokenUsageRecord]:
    return [
        r
        for _, r in CycleEventLog(ledger).iter()
        if isinstance(r, TokenUsageRecord) and r.input_tokens
    ]


def _scripted_lm(sends: list[int], script: list[Exception]) -> Any:
    import dspy  # extras: dspy
    from dspy import lm15

    class _Engine:
        def complete(self, _request: Any) -> Any:
            sends.append(1)
            if script:
                raise script.pop(0)
            said = lm15.TextPart("[[ ## answer ## ]]\n4\n\n[[ ## completed ## ]]")
            return lm15.Response(
                id=None,
                model="gpt-x",
                message=lm15.Message.assistant([said]),
                finish_reason="stop",
                usage=lm15.Usage(input_tokens=11, output_tokens=3),
            )

    return dspy.LM("openai/gpt-x", engine=_Engine(), cache=False)


def _dspy_cell(program: dspy_module.DspyProgram, max_calls: int, ledger: Path) -> dict[str, Any]:
    client = BackendClient(
        dspy_module.CONNECTOR,
        workload=InProcessWorkload(experiment=None, experiment_dir=None, program=program),
        jobs_dir=ledger.parent,
    )
    config = {
        "prompt": "Answer.",
        "model": "openai/gpt-x",
        "max_calls": max_calls,
        "max_tokens": 50,
    }
    pairs = {dspy_module.PROGRAM_NODE: client.priced_as(NodeCall.model_validate(config))}

    async def one_cell() -> Any:
        async with CellEnvelope(600.0, attempts=client.connector.cell_attempts, label="q"):
            return await client.run_query(
                Sample(id=0, query="q", ground_truth="4"),
                PipelineParams().with_nodes({dspy_module.PROGRAM_NODE: config}),
                bound=SendBound(input_tokens=20_000, output_tokens=2_000, usd=0.5),
                billed=cell_billing(pipeline_schema([]), pairs, {}),
            )

    # A real clock: the student runs in a worker thread, which a jumping clock outruns.
    with scoped(ledger=CycleEventLog(ledger)), scoped(book=spend_book(1.0)):
        data, _ = asyncio.run(one_cell())
    return dict(data)


# 1. A connector's cell survives its backend's quirks and its host's state, and bills every send.
def test_connector_cells(offline: Router, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A TermNorm reload under sixteen cells in flight re-sends the terms once and fails no cell.
    client = backend_client(tmp_path, session_factory=TermNormSession, max_cells_in_flight=16)
    rows = samples(16)
    offline.backend.truth = {s.query: s.ground_truth for s in rows}
    jumping_clock(client.init_session(rows, ["b"]))
    reloaded = httpx.Response(400, json={"code": "no_session", "message": "no session"})
    offline.backend.faults = {"/matches": [reloaded] * len(rows)}
    offline.backend.asked.clear()
    params = PipelineParams().with_nodes({"llm_only": {"prompt": "p", "model": "m"}})
    bound = SendBound(input_tokens=1_000, output_tokens=1_000, usd=0.01)

    async def cell(sample: Sample) -> Any:
        async with CellEnvelope(600.0, attempts=client.connector.cell_attempts, label="q"):
            return await client.run_query(
                sample,
                params,
                bound=bound,
                billed=cell_billing(pipeline_schema([]), {}, {"llm_only": bound}),
            )

    async def in_flight() -> list[Any]:
        return await asyncio.gather(*map(cell, rows), return_exceptions=True)

    with scoped(ledger=CycleEventLog(tmp_path / "cells.jsonl")), scoped(book=spend_book()):
        ended = jumping_clock(in_flight())
    failed = [e for e in ended if isinstance(e, BaseException)]
    assert not failed, f"{len(failed)} cells failed a reload the session recovers: {failed[:1]}"
    assert offline.backend.asked.count("/sessions") == 1, "one reload re-sent the terms per cell"
    assert offline.backend.asked.count("/matches") == 2 * len(rows), "a recovered cell resent twice"

    # A resent DSPy call spends one of its program's `max_calls`.
    import dspy  # extras: dspy
    from dspy import lm15

    offline.rates = {"openai/gpt-x": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6}}
    assert pricing.refresh_rates(force=True), "the world's price list was not fetched"
    monkeypatch.setattr(dspy_module, "held_wait", _no_wait)
    sends: list[int] = []
    lm = _scripted_lm(sends, [lm15.ServerError("boom", status=500)])

    class _TwoCalls(dspy.Module):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            self.steps = [dspy.Predict("question -> answer") for _ in range(2)]
            for step in self.steps:
                step.lm = lm

        def forward(self, question: str) -> Any:
            for step in self.steps:
                answer = step(question=question)
            return answer

    example = dspy.Example(question="2+2?", answer="4").with_inputs("question")
    program = dspy_module.DspyProgram(
        student=_TwoCalls(),
        metric=lambda ex, pred: pred.answer == ex.answer,
        examples=[example],
    )
    data = _dspy_cell(program, 2, tmp_path / "resent.jsonl")
    assert data[dspy_module.SCORE_KEY] == 1.0, "a 5xx spent the call its resend answered"
    assert len(sends) == 3, f"{len(sends)} sends: two calls, one resent once"
    assert len(_lm_bills(tmp_path / "resent.jsonl")) == 2, "an answered call left no bill"
