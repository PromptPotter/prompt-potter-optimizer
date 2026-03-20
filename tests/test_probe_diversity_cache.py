"""Tests for probe round intelligence, axis diversity, and per-query caching."""
import hashlib
import json

import pytest

from api.models.hashing import HASH_TRUNCATE
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
# Issue 2: Per-query caching
# ---------------------------------------------------------------------------


class TestFindQueryResults:
    """DatasetRunStore.find_query_results returns per-query results across runs."""

    @pytest.fixture()
    def drs(self, tmp_path):
        return DatasetRunStore(tmp_path)

    def _make_run(self, run_id, rp_text, queries, model="m1",
                  temperature=0.5, pipeline_params=None, item_count=None):
        items = [
            {"query": q, "predicted": pred, "ground_truth": gt,
             "hit": pred == gt, "score": 1.0 if pred == gt else 0.0, "error": None}
            for q, pred, gt in queries
        ]
        if item_count is None:
            item_count = len(items)
        rph = hashlib.sha256(rp_text.encode()).hexdigest()[:HASH_TRUNCATE]
        run: dict = {
            "run_id": run_id,
            "name": run_id,
            "content_hash": f"ch_{run_id}",
            "prompt_state_id": "ps1",
            "rendered_prompt_hash": rph,
            "model": model,
            "temperature": temperature,
            "item_count": item_count,
            "scores": {"accuracy": 0.5, "hits": 1, "total": item_count},
            "source": "test",
            "created_at": "2026-01-01T00:00:00Z",
            "dataset_run_items": items,
        }
        if pipeline_params:
            run["pipeline_params"] = pipeline_params
        return run, rph

    def test_finds_queries_across_different_item_counts(self, drs):
        """Runs with same config but different item_count share query results."""
        run1, rph = self._make_run(
            "r1", "prompt A",
            [("q1", "A", "A"), ("q2", "B", "B")],
        )
        run2, _ = self._make_run(
            "r2", "prompt A",
            [("q2", "B", "B"), ("q3", "C", "C"), ("q4", "D", "D")],
        )
        drs.save("b1", "r1", run1)
        drs.save("b1", "r2", run2)

        results = drs.find_query_results("b1", rph, "m1", 0.5, None)
        assert "q1" in results
        assert "q2" in results
        assert "q3" in results
        assert "q4" in results

    def test_filters_by_model(self, drs):
        """Different model → no results."""
        run, rph = self._make_run("r1", "prompt A", [("q1", "A", "A")])
        drs.save("b1", "r1", run)

        results = drs.find_query_results("b1", rph, "wrong_model", 0.5, None)
        assert results == {}

    def test_filters_by_temperature(self, drs):
        """Different temperature → no results."""
        run, rph = self._make_run("r1", "prompt A", [("q1", "A", "A")])
        drs.save("b1", "r1", run)

        results = drs.find_query_results("b1", rph, "m1", 0.9, None)
        assert results == {}

    def test_filters_by_pipeline_params(self, drs):
        """Different pipeline_params → no results."""
        pp = {"ranking_temperature": 0.3}
        run, rph = self._make_run(
            "r1", "prompt A", [("q1", "A", "A")], pipeline_params=pp,
        )
        drs.save("b1", "r1", run)

        results = drs.find_query_results("b1", rph, "m1", 0.5, {"ranking_temperature": 0.9})
        assert results == {}

    def test_steps_only_difference_matches(self, drs):
        """Steps-only pipeline_params difference still matches (steps stripped)."""
        pp_stored = {"steps": ["a"], "ranking_temperature": 0.3}
        run, rph = self._make_run(
            "r1", "prompt A", [("q1", "A", "A")], pipeline_params=pp_stored,
        )
        drs.save("b1", "r1", run)

        pp_query = {"steps": ["b"], "ranking_temperature": 0.3}
        results = drs.find_query_results("b1", rph, "m1", 0.5, pp_query)
        assert "q1" in results

    def test_later_run_overwrites_query(self, drs):
        """Same query in later run overwrites earlier result."""
        run1, rph = self._make_run(
            "r1", "prompt A", [("q1", "WRONG", "A")],
        )
        run2, _ = self._make_run(
            "r2", "prompt A", [("q1", "A", "A")],
        )
        drs.save("b1", "r1", run1)
        drs.save("b1", "r2", run2)

        results = drs.find_query_results("b1", rph, "m1", 0.5, None)
        assert results["q1"]["predicted"] == "A"

    def test_alias_resolution(self, drs):
        """Finds results via alias group."""
        run, _ = self._make_run("r1", "prompt A", [("q1", "A", "A")])
        drs.save("b1", "r1", run)

        rph_a = hashlib.sha256(b"prompt A").hexdigest()[:HASH_TRUNCATE]
        rph_b = hashlib.sha256(b"prompt B").hexdigest()[:HASH_TRUNCATE]
        drs.register_alias("b1", rph_a, rph_b)

        results = drs.find_query_results("b1", rph_b, "m1", 0.5, None)
        assert "q1" in results


class TestFindQueryResultsIgnorePrompt:
    """ignore_prompt=True matches across different prompts when pipeline_params match."""

    @pytest.fixture()
    def drs(self, tmp_path):
        return DatasetRunStore(tmp_path)

    def _make_run(self, run_id, rp_text, queries, model="m1",
                  temperature=0.5, pipeline_params=None):
        items = [
            {"query": q, "predicted": pred, "ground_truth": gt,
             "hit": pred == gt, "score": 1.0 if pred == gt else 0.0, "error": None}
            for q, pred, gt in queries
        ]
        rph = hashlib.sha256(rp_text.encode()).hexdigest()[:HASH_TRUNCATE]
        run: dict = {
            "run_id": run_id, "name": run_id,
            "content_hash": f"ch_{run_id}", "prompt_state_id": "ps1",
            "rendered_prompt_hash": rph, "model": model,
            "temperature": temperature, "item_count": len(items),
            "scores": {"accuracy": 0.5, "hits": 1, "total": len(items)},
            "source": "test", "created_at": "2026-01-01T00:00:00Z",
            "dataset_run_items": items,
        }
        if pipeline_params is not None:
            run["pipeline_params"] = pipeline_params
        return run, rph

    def test_ignore_prompt_finds_across_different_prompts(self, drs):
        """With ignore_prompt=True, different prompts match if pipeline_params match."""
        pp = {
            "steps": ["web_search", "token_matching"],
            "token_matching": {"max_token_candidates": 30},
        }
        run1, _ = self._make_run("r1", "prompt A", [("q1", "A", "A")], pipeline_params=pp)
        drs.save("b1", "r1", run1)

        rph_b = hashlib.sha256(b"prompt B").hexdigest()[:HASH_TRUNCATE]
        results = drs.find_query_results("b1", rph_b, "m1", 0.5, pp, ignore_prompt=True)
        assert "q1" in results

    def test_ignore_prompt_rejects_different_pipeline_params(self, drs):
        """With ignore_prompt=True, different pipeline_params → no match."""
        pp1 = {
            "steps": ["web_search", "token_matching"],
            "token_matching": {"max_token_candidates": 30},
        }
        pp2 = {
            "steps": ["web_search", "token_matching"],
            "token_matching": {"max_token_candidates": 50},
        }
        run1, _ = self._make_run("r1", "prompt A", [("q1", "A", "A")], pipeline_params=pp1)
        drs.save("b1", "r1", run1)

        rph_b = hashlib.sha256(b"prompt B").hexdigest()[:HASH_TRUNCATE]
        results = drs.find_query_results("b1", rph_b, "m1", 0.5, pp2, ignore_prompt=True)
        assert results == {}

    def test_ignore_prompt_compares_steps(self, drs):
        """With ignore_prompt=True, different steps → no match (steps affect results)."""
        pp1 = {"steps": ["web_search", "token_matching"]}
        pp2 = {"steps": ["web_search", "entity_profiling", "token_matching"]}
        run1, _ = self._make_run("r1", "prompt A", [("q1", "A", "A")], pipeline_params=pp1)
        drs.save("b1", "r1", run1)

        rph_b = hashlib.sha256(b"prompt B").hexdigest()[:HASH_TRUNCATE]
        results = drs.find_query_results("b1", rph_b, "m1", 0.5, pp2, ignore_prompt=True)
        assert results == {}

    def test_ignore_prompt_false_requires_rp_hash(self, drs):
        """With ignore_prompt=False (default), different prompts → no match."""
        pp = {"steps": ["web_search", "token_matching"]}
        run1, _ = self._make_run("r1", "prompt A", [("q1", "A", "A")], pipeline_params=pp)
        drs.save("b1", "r1", run1)

        rph_b = hashlib.sha256(b"prompt B").hexdigest()[:HASH_TRUNCATE]
        results = drs.find_query_results("b1", rph_b, "m1", 0.5, pp, ignore_prompt=False)
        assert results == {}

    def test_ignore_prompt_aggregates_across_runs(self, drs):
        """Prompt-independent lookup aggregates queries from multiple prior runs."""
        pp = {"steps": ["token_matching"]}
        run1, _ = self._make_run("r1", "prompt A", [("q1", "A", "A")], pipeline_params=pp)
        run2, _ = self._make_run("r2", "prompt B", [("q2", "B", "B")], pipeline_params=pp)
        drs.save("b1", "r1", run1)
        drs.save("b1", "r2", run2)

        rph_c = hashlib.sha256(b"prompt C").hexdigest()[:HASH_TRUNCATE]
        results = drs.find_query_results("b1", rph_c, "m1", 0.5, pp, ignore_prompt=True)
        assert "q1" in results
        assert "q2" in results


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
            cached_query_results=cached,
        )

        assert len(results) == 2
        # First query should use cache with cached marker
        assert results[0]["predicted"] == "Aspirin"
        assert results[0]["hit"] is True
        assert results[0]["cached"] is True
        assert backend.run_match.call_count == 1
        # The backend call should be for ibuprofen only
        assert backend.run_match.call_args[0][0] == "ibuprofen"
        # Fresh result should not have cached marker
        assert "cached" not in results[1]
