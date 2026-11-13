"""The built-in connectors' own faults: what a backend's quirk or a host's state costs a cell."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from factories import (
    backend_client,
    jumping_clock,
    pipeline_schema,
    samples,
    spend_book,
    workspace,
)
from offline_world import Router

from promptpotter.application.scoring.cell_envelope import CellEnvelope
from promptpotter.application.scoring.sample_measurement import cell_billing
from promptpotter.config.settings import settings
from promptpotter.connectors import dbllmbench, dspy_module, harbor, n8n
from promptpotter.connectors.protocol import InProcessWorkload
from promptpotter.connectors.termnorm import TermNormSession
from promptpotter.domain.pipeline_params import NodeCall, PipelineParams
from promptpotter.domain.run_records import TokenUsageRecord
from promptpotter.domain.sample import Sample
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm import pricing
from promptpotter.infrastructure.llm.spend_book import SendBound
from promptpotter.shared.errors import CellInfrastructureError
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
                usage=lm15.Usage(input_tokens=11, output_tokens=3, reasoning_tokens=2),
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


def _priced(offline: Router) -> None:
    offline.rates = {"openai/gpt-x": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6}}
    assert pricing.refresh_rates(force=True), "the world's price list was not fetched"


def _program(lm: Any, calls: int, metric: Any) -> dspy_module.DspyProgram:
    """A student of *calls* `Predict` steps, each sending on *lm*, judged by *metric*."""
    import dspy  # extras: dspy

    class _Calls(dspy.Module):  # type: ignore[misc]
        def __init__(self, calls: int) -> None:
            super().__init__()
            self.steps = [dspy.Predict("question -> answer") for _ in range(calls)]
            for step in self.steps:
                step.lm = lm

        def forward(self, question: str) -> Any:
            for step in self.steps:
                answer = step(question=question)
            return answer

    example = dspy.Example(question="2+2?", answer="4").with_inputs("question")
    return dspy_module.DspyProgram(student=_Calls(calls), metric=metric, examples=[example])


# 1. A connector's cell survives its backend's quirks and its host's state, and bills every send.


def test_a_termnorm_reload_under_sixteen_cells_resends_the_terms_once_and_fails_no_cell(
    offline: Router, tmp_path: Path
) -> None:
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


def test_a_resent_dspy_call_spends_one_of_its_programs_max_calls(
    offline: Router, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dspy import lm15

    _priced(offline)
    monkeypatch.setattr(dspy_module, "held_wait", _no_wait)
    sends: list[int] = []
    lm = _scripted_lm(sends, [lm15.ServerError("boom", status=500)])
    program = _program(lm, 2, lambda ex, pred: pred.answer == ex.answer)
    data = _dspy_cell(program, 2, tmp_path / "resent.jsonl")
    assert data[dspy_module.SCORE_KEY] == 1.0, "a 5xx spent the call its resend answered"
    assert len(sends) == 3, f"{len(sends)} sends: two calls, one resent once"
    bills = _lm_bills(tmp_path / "resent.jsonl")
    assert [bill.reasoning_tokens for bill in bills] == [2, 2], (
        "an answered call's bill dropped its reasoning tokens, or the call left no bill"
    )


def test_an_llm_judge_metrics_send_is_the_cells_billed_beside_the_programs(
    offline: Router, tmp_path: Path
) -> None:
    import dspy  # extras: dspy

    _priced(offline)
    sends: list[int] = []
    judge = _scripted_lm(sends, [])

    def judged(_example: Any, prediction: Any) -> float:
        return float(bool(dspy.settings.lm(f"Is {prediction.answer} right?")))

    program = _program(_scripted_lm(sends, []), 1, judged)
    with dspy.context(lm=judge):
        data = _dspy_cell(program, 2, tmp_path / "judged.jsonl")
    assert data[dspy_module.SCORE_KEY] == 1.0, "the judge's verdict is not the cell's score"
    assert len(_lm_bills(tmp_path / "judged.jsonl")) == 2, "the judge's send went unbilled"


# The host's state is read per cell.
@pytest.mark.usefixtures("offline")
def test_a_dbllmbench_image_rebuilt_under_its_tag_fails_the_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = ["abc1234"]

    async def inspected(*_args: str, **_kw: Any) -> tuple[int, str]:
        return 0, built[0]

    monkeypatch.setattr(dbllmbench, "docker", inspected)
    image = "pp-dbllmbench:abc1234-search"
    jumping_clock(dbllmbench._check_image(image))
    built[0] = "f00d000"
    with pytest.raises(CellInfrastructureError):
        jumping_clock(dbllmbench._check_image(image))


@pytest.mark.usefixtures("offline")
def test_a_harbor_roster_repinned_under_a_live_server_resolves_as_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(harbor, "_ROSTER_DIR", tmp_path)
    resolve = harbor.CONNECTOR.resolve_experiment
    assert resolve is not None
    stores = workspace(tmp_path / "harbor")
    panel = {"harbor_dataset": "bench", "harbor_dataset_version": "1.0"}
    for pinned in (["t1"], ["t2", "t3"]):
        tasks = [{"id": t, "path": t} for t in pinned]
        (tmp_path / "harbor-roster.bench@1.0.json").write_text(
            json.dumps({"tasks": tasks}), encoding="utf-8"
        )
        resolved = [t["id"] for t in resolve(stores, panel)["tasks"]]
        assert resolved == pinned, f"a live server resolved {resolved} off a re-pinned roster"


# An n8n cell is keyed on the URL the wire posts to.
@pytest.mark.usefixtures("offline")
def test_a_trailing_slash_on_the_n8n_harness_url_rekeys_no_cell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stores = workspace(tmp_path)
    harness = n8n.CONNECTOR.identity_config
    assert harness is not None
    keys = []
    for url in ("https://n8n.example/webhook/eval", "https://n8n.example/webhook/eval/"):
        monkeypatch.setattr(settings, "N8N_HARNESS_URL", url)
        keys.append(harness(stores, None))
    assert keys[0] == keys[1], "a trailing slash re-keyed every banked n8n cell"
