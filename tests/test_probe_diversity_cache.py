"""Tests for probe round intelligence, axis diversity, and SP-hash caching."""
import json

import pytest
from _helpers import make_dataset_run

from api.models.opt_search_point import OptSearchPoint
from api.services.l1_optimizer import l1_generate
from api.services.stores.dataset_run_store import DatasetRunStore


def _capture_llm_prompts(monkeypatch):
    """Mock llm_call and return list of captured system prompts."""
    captured: list[str] = []

    async def mock_llm_call(client, messages, *, node=None, config=None, model=None, temperature=None):
        captured.append(messages[0]["content"])
        _parsed = {"variants": [{"instruction": "v1", "variant_name": "v1"}]}

        class R:
            parsed = _parsed
            content = json.dumps(_parsed)
        return R()

    monkeypatch.setattr("api.services.l1_optimizer.llm_call", mock_llm_call)
    return captured


class TestProbeRoundEnrichment:

    @pytest.fixture()
    def warning_inventory(self):
        return {
            "aspirin": {
                "rounds_seen": 3,
                "hits": 1,
                "misses": 2,
                "warnings": {"web_search:timeout": 3, "entity_profiling:no_match": 1},
                "last_terminated_at": "token_matching",
            },
            "ibuprofen": {
                "rounds_seen": 3,
                "hits": 0,
                "misses": 3,
                "warnings": {"web_search:timeout": 2},
                "last_terminated_at": "llm_ranking",
            },
        }

    @pytest.fixture()
    def escalation_journal(self):
        return [
            {
                "problem_step": "web_search",
                "degraded_rate": 0.4,
                "warning_types": {"web_search:timeout": 5},
            },
        ]

    @pytest.mark.asyncio
    async def test_probe_meta_prompt_contains_warnings_and_journal(
        self, monkeypatch, warning_inventory, escalation_journal,
    ):
        captured = _capture_llm_prompts(monkeypatch)

        osp = OptSearchPoint(instruction="test prompt",
                              warning_inventory=warning_inventory,
                              escalation_journal=escalation_journal)
        await l1_generate(
            osp, 0.5, [
                {"query": "aspirin", "predicted": "wrong", "ground_truth": "Aspirin", "hit": False},
            ],
            n_variants=1, creativity=0.5, llm_client=None,
            is_probe_round=True,
        )

        prompt = captured[0]
        assert "RECURRING PIPELINE WARNINGS" in prompt
        assert "web_search:timeout" in prompt
        assert "Dominant problem step: web_search" in prompt
        assert "Previous attempts targeting web_search" in prompt
        assert "degraded_rate=40%" in prompt

    @pytest.mark.asyncio
    @pytest.mark.parametrize("is_probe,expect_annotation", [(True, True), (False, False)])
    async def test_annotation_threshold_depends_on_probe(
        self, monkeypatch, is_probe, expect_annotation,
    ):
        captured = _capture_llm_prompts(monkeypatch)

        inv = {
            "aspirin": {
                "rounds_seen": 1, "hits": 0, "misses": 1,
                "warnings": {"web_search:timeout": 1},
                "last_terminated_at": "token_matching",
            },
        }

        osp = OptSearchPoint(instruction="test prompt", warning_inventory=inv)
        await l1_generate(
            osp, 0.5, [
                {"query": "aspirin", "predicted": "wrong", "ground_truth": "Aspirin", "hit": False},
            ],
            n_variants=1, creativity=0.5, llm_client=None,
            is_probe_round=is_probe,
        )

        if expect_annotation:
            assert "[web_search:timeout 1/1 rounds]" in captured[0]
        else:
            assert "[web_search:timeout" not in captured[0]


class TestValueDiversity:

    @pytest.mark.asyncio
    async def test_diversity_in_thinking_style(self, monkeypatch):
        captured = _capture_llm_prompts(monkeypatch)

        osp = OptSearchPoint(instruction="test")
        await l1_generate(
            osp, 0.5, [
                {"query": "q", "predicted": "w", "ground_truth": "g",
                 "hit": False},
            ],
            n_variants=1, creativity=0.5, llm_client=None,
        )

        assert "Maximize value diversity" in captured[0]
        assert "Candidate 1 → focus on:" not in captured[0]


class TestFindCachedQueries:

    @pytest.fixture()
    def drs(self, tmp_path):
        return DatasetRunStore(tmp_path)

    def _make_run(self, run_id, sp_hash, queries, pipeline_params=None):
        items = [
            {"query": q, "predicted": pred, "ground_truth": gt,
             "hit": pred == gt, "score": 1.0 if pred == gt else 0.0, "error": None}
            for q, pred, gt in queries
        ]
        hits = sum(1 for it in items if it.get("hit"))
        total = len(items)
        run = make_dataset_run(
            run_id, accuracy=hits / total if total else 0.0,
            items=items, content_hash=f"ch_{run_id}",
        )
        run["sp_hash"] = sp_hash
        run["rendered_prompt_hash"] = "rph1"
        run["model"] = "m1"
        run["source"] = "test"
        if pipeline_params:
            run["pipeline_params"] = pipeline_params
        return run

    def test_finds_queries_across_different_sample_sizes(self, drs):
        run1 = self._make_run("r1", "sp_aaa", [("q1", "A", "A"), ("q2", "B", "B")])
        run2 = self._make_run("r2", "sp_aaa", [("q3", "C", "C"), ("q4", "D", "D")])
        drs.save("b1", "r1", run1)
        drs.save("b1", "r2", run2)

        results = drs.find_cached_queries("b1", "sp_aaa")
        assert set(results.keys()) == {"q1", "q2", "q3", "q4"}

    def test_different_sp_hash_no_match(self, drs):
        run = self._make_run("r1", "sp_aaa", [("q1", "A", "A")])
        drs.save("b1", "r1", run)

        results = drs.find_cached_queries("b1", "sp_bbb")
        assert results == {}

    def test_later_run_overwrites_query(self, drs):
        run1 = self._make_run("r1", "sp_aaa", [("q1", "WRONG", "A")])
        run2 = self._make_run("r2", "sp_aaa", [("q1", "A", "A")])
        drs.save("b1", "r1", run1)
        drs.save("b1", "r2", run2)

        results = drs.find_cached_queries("b1", "sp_aaa")
        assert results["q1"]["predicted"] == "A"


class TestEvaluatePromptBatchCaching:

    @pytest.mark.asyncio
    async def test_cached_queries_skip_backend(self):
        from unittest.mock import AsyncMock

        from api.models.search_point import JobSearchPoint
        from api.services.prompt_eval import _run_eval_batch

        sp = JobSearchPoint(
            pipeline_params={"llm_ranking": {"prompt": "test"}},
        )

        backend = AsyncMock()
        backend.run_match.return_value = {
            "data": {
                "ranked_candidates": [{"candidate": "Aspirin"}],
                "step_timings": {},
            },
        }

        eval_data = [
            {"query": "aspirin", "ground_truth": "Aspirin"},
            {"query": "ibuprofen", "ground_truth": "Ibuprofen"},
        ]

        cached = {
            "aspirin": {
                "query": "aspirin", "predicted": "Aspirin",
                "ground_truth": "Aspirin", "hit": True,
                "score": 1.0, "error": None, "pipeline_data": {},
            },
        }

        batch = await _run_eval_batch(
            sp, eval_data, backend,
            cached_queries=cached,
        )
        results = batch.results

        assert len(results) == 2
        assert results[0]["predicted"] == "Aspirin"
        assert results[0]["hit"] is True
        assert results[0]["cached"] is True
        assert backend.run_match.call_count == 1
        assert backend.run_match.call_args[0][0] == "ibuprofen"
        assert "cached" not in results[1]
