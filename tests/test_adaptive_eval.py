"""Tests for Wave 1d adaptive eval set sampling."""

from api.models.analysis import QueryDifficulty, QueryProfile
from api.services.campaign.adaptive_eval import adapt_eval_set


def _make_eval_data(queries: list[str]) -> list[dict]:
    return [{"query": q, "ground_truth": f"gt_{q}"} for q in queries]


def _make_difficulty(
    *,
    dead: list[str] | None = None,
    discriminating: list[str] | None = None,
    easy: list[str] | None = None,
    hard: list[str] | None = None,
) -> QueryDifficulty:
    profiles = []
    for q in (dead or []):
        profiles.append(QueryProfile(query=q, hit_rate=1.0, n_evaluations=3, classification="dead"))
    for q in (discriminating or []):
        profiles.append(QueryProfile(query=q, hit_rate=0.5, n_evaluations=3, classification="discriminating"))
    for q in (easy or []):
        profiles.append(QueryProfile(query=q, hit_rate=0.95, n_evaluations=3, classification="easy"))
    for q in (hard or []):
        profiles.append(QueryProfile(query=q, hit_rate=0.05, n_evaluations=3, classification="hard"))
    return QueryDifficulty(profiles=profiles)


class TestAdaptEvalSet:
    def test_replaces_dead_with_discriminating(self):
        current = _make_eval_data(["q0", "q1", "q2", "q3"])
        full_pool = _make_eval_data(["q0", "q1", "q2", "q3", "q4", "q5"])
        qd = _make_difficulty(dead=["q0"], discriminating=["q4", "q5"])

        new_data, summary = adapt_eval_set(current, qd, full_pool)

        assert summary["dropped"] == 1
        assert summary["added"] == 1
        assert len(new_data) == 4
        new_queries = {d["query"] for d in new_data}
        assert "q0" not in new_queries
        assert len(new_queries & {"q4", "q5"}) == 1

    def test_no_dead_queries_unchanged(self):
        current = _make_eval_data(["q0", "q1"])
        full_pool = _make_eval_data(["q0", "q1", "q2"])
        qd = _make_difficulty(discriminating=["q0", "q1", "q2"])

        new_data, summary = adapt_eval_set(current, qd, full_pool)

        assert summary["unchanged"] is True
        assert new_data == current

    def test_max_drop_fraction(self):
        # 8 queries, 4 dead -> max drop = 2 (25% of 8)
        queries = [f"q{i}" for i in range(8)]
        current = _make_eval_data(queries)
        full_pool = _make_eval_data(queries + [f"r{i}" for i in range(4)])
        qd = _make_difficulty(
            dead=["q0", "q1", "q2", "q3"],
            discriminating=[f"r{i}" for i in range(4)],
        )

        new_data, summary = adapt_eval_set(current, qd, full_pool)

        assert summary["dropped"] == 2  # capped at 25% of 8
        assert len(new_data) == 8

    def test_no_replacements_available(self):
        current = _make_eval_data(["q0", "q1"])
        full_pool = _make_eval_data(["q0", "q1"])
        qd = _make_difficulty(dead=["q0"])

        _new_data, summary = adapt_eval_set(current, qd, full_pool)

        # No discriminating queries in pool -> no changes
        assert summary["unchanged"] is True

    def test_only_drops_as_many_as_can_replace(self):
        current = _make_eval_data(["q0", "q1", "q2", "q3"])
        full_pool = _make_eval_data(["q0", "q1", "q2", "q3", "r0"])
        qd = _make_difficulty(dead=["q0", "q1"], discriminating=["r0"])

        new_data, summary = adapt_eval_set(current, qd, full_pool)

        # Only 1 replacement available -> only drop 1
        assert summary["dropped"] == 1
        assert summary["added"] == 1
        assert len(new_data) == 4
