"""Tests for probe round intelligence, axis diversity, and SP-hash caching."""
import json

import pytest

from api.models.prompt_state import PromptState
from api.services.prompt_optimizer import l1_generate
from api.services.stores.dataset_run_store import DatasetRunStore


# ---------------------------------------------------------------------------
# Issue 1: Probe round enrichment
# ---------------------------------------------------------------------------


def _capture_llm_prompts(monkeypatch):
    """Mock llm_call and return list of captured system prompts."""
    captured: list[str] = []

    async def mock_llm_call(client, *, messages, config, model=None, temperature=None):
        captured.append(messages[0]["content"])
        _parsed = {"variants": [{"instruction": "v1", "variant_name": "v1"}]}

        class R:
            parsed = _parsed
            content = json.dumps(_parsed)
        return R()

    monkeypatch.setattr("api.services.prompt_optimizer.llm_call", mock_llm_call)
    return captured


class TestProbeRoundEnrichment:
    """l1_generate() with is_probe_round=True injects structured signal."""

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
    async def test_probe_meta_prompt_contains_warning_summary(
        self, monkeypatch, warning_inventory,
    ):
        """Probe round meta-prompt includes summarize_warning_inventory output."""
        captured = _capture_llm_prompts(monkeypatch)

        ps = PromptState(instruction="test prompt")
        await l1_generate(
            ps, 0.5, [
                {"query": "aspirin", "predicted": "wrong", "ground_truth": "Aspirin", "hit": False},
            ],
            n_variants=1, creativity=0.5, llm_client=None,
            warning_inventory=warning_inventory,
            is_probe_round=True,
        )

        prompt = captured[0]
        assert "RECURRING PIPELINE WARNINGS" in prompt
        assert "web_search:timeout" in prompt
        assert "Dominant problem step: web_search" in prompt

    @pytest.mark.asyncio
    async def test_probe_includes_escalation_journal(
        self, monkeypatch, warning_inventory, escalation_journal,
    ):
        """Probe round includes tried configs from escalation journal."""
        captured = _capture_llm_prompts(monkeypatch)

        ps = PromptState(instruction="test prompt")
        await l1_generate(
            ps, 0.5, [
                {"query": "aspirin", "predicted": "wrong", "ground_truth": "Aspirin", "hit": False},
            ],
            n_variants=1, creativity=0.5, llm_client=None,
            warning_inventory=warning_inventory,
            escalation_journal=escalation_journal,
            is_probe_round=True,
        )

        prompt = captured[0]
        assert "Previous attempts targeting web_search" in prompt
        assert "degraded_rate=40%" in prompt

    @pytest.mark.asyncio
    async def test_probe_lowers_annotation_threshold(
        self, monkeypatch, warning_inventory,
    ):
        """During probe, single-occurrence warnings are annotated on failure examples."""
        captured = _capture_llm_prompts(monkeypatch)

        # Give aspirin a single-count warning
        inv = {
            "aspirin": {
                "rounds_seen": 1, "hits": 0, "misses": 1,
                "warnings": {"web_search:timeout": 1},
                "last_terminated_at": "token_matching",
            },
        }

        ps = PromptState(instruction="test prompt")
        await l1_generate(
            ps, 0.5, [
                {"query": "aspirin", "predicted": "wrong", "ground_truth": "Aspirin", "hit": False},
            ],
            n_variants=1, creativity=0.5, llm_client=None,
            warning_inventory=inv,
            is_probe_round=True,
        )

        assert "[web_search:timeout 1/1 rounds]" in captured[0]

    @pytest.mark.asyncio
    async def test_non_probe_does_not_annotate_single_occurrence(
        self, monkeypatch,
    ):
        """Outside probe, single-occurrence warnings are NOT annotated."""
        captured = _capture_llm_prompts(monkeypatch)

        inv = {
            "aspirin": {
                "rounds_seen": 1, "hits": 0, "misses": 1,
                "warnings": {"web_search:timeout": 1},
                "last_terminated_at": "token_matching",
            },
        }

        ps = PromptState(instruction="test prompt")
        await l1_generate(
            ps, 0.5, [
                {"query": "aspirin", "predicted": "wrong", "ground_truth": "Aspirin", "hit": False},
            ],
            n_variants=1, creativity=0.5, llm_client=None,
            warning_inventory=inv,
            is_probe_round=False,
        )

        assert "[web_search:timeout" not in captured[0]


# ---------------------------------------------------------------------------
# Issue 3: Value diversity (template-driven, no rigid axis assignment)
# ---------------------------------------------------------------------------


class TestValueDiversity:
    """Thinking style instructs LLM to maximize value diversity."""

    @pytest.mark.asyncio
    async def test_diversity_in_thinking_style(self, monkeypatch):
        """Meta-prompt thinking_style includes value diversity instruction."""
        captured = _capture_llm_prompts(monkeypatch)

        ps = PromptState(instruction="test")
        await l1_generate(
            ps, 0.5, [
                {"query": "q", "predicted": "w", "ground_truth": "g",
                 "hit": False},
            ],
            n_variants=1, creativity=0.5, llm_client=None,
        )

        assert "Maximize value diversity" in captured[0]
        assert "Candidate 1 → focus on:" not in captured[0]


# ---------------------------------------------------------------------------
# Issue 2: SP-hash query caching (find_cached_queries)
# ---------------------------------------------------------------------------


class TestFindCachedQueries:
    """DatasetRunStore.find_cached_queries returns per-query results by SP hash."""

    @pytest.fixture()
    def drs(self, tmp_path):
        return DatasetRunStore(tmp_path)

    def _make_run(self, run_id, sp_hash, queries, pipeline_params=None):
        items = [
            {"query": q, "predicted": pred, "ground_truth": gt,
             "hit": pred == gt, "score": 1.0 if pred == gt else 0.0, "error": None}
            for q, pred, gt in queries
        ]
        run: dict = {
            "run_id": run_id,
            "name": run_id,
            "content_hash": f"ch_{run_id}",
            "prompt_state_id": "ps1",
            "rendered_prompt_hash": "rph1",
            "sp_hash": sp_hash,
            "model": "m1",
            "temperature": 0.5,
            "item_count": len(items),
            "scores": {"accuracy": 0.5, "hits": 1, "total": len(items)},
            "source": "test",
            "created_at": "2026-01-01T00:00:00Z",
            "dataset_run_items": items,
        }
        if pipeline_params:
            run["pipeline_params"] = pipeline_params
        return run

    def test_finds_queries_across_different_sample_sizes(self, drs):
        """Runs with same SP hash but different sample sizes share queries."""
        run1 = self._make_run("r1", "sp_aaa", [("q1", "A", "A"), ("q2", "B", "B")])
        run2 = self._make_run("r2", "sp_aaa", [("q3", "C", "C"), ("q4", "D", "D")])
        drs.save("b1", "r1", run1)
        drs.save("b1", "r2", run2)

        results = drs.find_cached_queries("b1", "sp_aaa")
        assert set(results.keys()) == {"q1", "q2", "q3", "q4"}

    def test_different_sp_hash_no_match(self, drs):
        """Different SP hash -> no results."""
        run = self._make_run("r1", "sp_aaa", [("q1", "A", "A")])
        drs.save("b1", "r1", run)

        results = drs.find_cached_queries("b1", "sp_bbb")
        assert results == {}

    def test_later_run_overwrites_query(self, drs):
        """Same query in later run overwrites earlier result."""
        run1 = self._make_run("r1", "sp_aaa", [("q1", "WRONG", "A")])
        run2 = self._make_run("r2", "sp_aaa", [("q1", "A", "A")])
        drs.save("b1", "r1", run1)
        drs.save("b1", "r2", run2)

        results = drs.find_cached_queries("b1", "sp_aaa")
        assert results["q1"]["predicted"] == "A"


class TestEvaluatePromptBatchCaching:
    """evaluate_prompt_batch skips backend calls for cached queries."""

    def test_cached_queries_skip_backend(self):
        from unittest.mock import MagicMock
        from api.models.prompt_state import PromptState
        from api.models.search_point import SearchPoint
        from api.services.prompt_eval import evaluate_prompt_batch

        ps = PromptState(instruction="test")
        sp = SearchPoint(prompt_state=ps, model="m1", temperature=0.0)

        backend = MagicMock()
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

        results = evaluate_prompt_batch(
            sp, eval_data, backend,
            cached_queries=cached,
        )

        assert len(results) == 2
        assert results[0]["predicted"] == "Aspirin"
        assert results[0]["hit"] is True
        assert results[0]["cached"] is True
        assert backend.run_match.call_count == 1
        assert backend.run_match.call_args[0][0] == "ibuprofen"
        assert "cached" not in results[1]
