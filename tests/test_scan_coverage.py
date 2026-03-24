"""Tests for assess_scan_coverage()."""

from api.services.search import assess_scan_coverage

from _helpers import rp_hash as _rp_hash, make_baseline_osp as _make_baseline


def _diagnostic(n: int = 6) -> list[dict]:
    return [{"query": f"q{i}", "ground_truth": f"gt{i}"} for i in range(n)]


def _build_index(entries: dict[str, list[str]]) -> dict[str, dict[str, dict]]:
    """Build a fake index: rendered_text -> list of covered query strings."""
    index: dict[str, dict[str, dict]] = {}
    for rendered_text, queries in entries.items():
        rp_hash = _rp_hash(rendered_text)
        index[rp_hash] = {q: {"query": q, "hit": True} for q in queries}
    return index


VARIANT_LIBRARY = {
    "prompt_fields": {
        "persona": [
            "",
            "You are a domain expert.",
            "You are a precise system.",
            "You are a careful assistant.",
        ],
        "task_intent": [
            "",
            "Identify the best match.",
            "Rank candidates by relevance.",
        ],
    },
    "pipeline_params": {
        "ranking_temperature": [0.0, 0.3, 0.7],
    },
}


def test_full_coverage_all_prompt_fields_satisfied():
    # Pipeline-param axes are never satisfied by index, so all_satisfied stays False.
    baseline = _make_baseline()
    diag = _diagnostic(6)
    query_strings = [d["query"] for d in diag]

    # Build index with full coverage for baseline + all persona/task_intent variants
    entries: dict[str, list[str]] = {}
    entries[baseline.render()] = query_strings

    for persona_val in ["You are a domain expert.", "You are a precise system.",
                        "You are a careful assistant."]:
        rendered = baseline.derive_candidate(persona=persona_val).render()
        entries[rendered] = query_strings

    for ti_val in ["Identify the best match.", "Rank candidates by relevance."]:
        rendered = baseline.derive_candidate(task_intent=ti_val).render()
        entries[rendered] = query_strings

    index = _build_index(entries)
    result = assess_scan_coverage(
        baseline, VARIANT_LIBRARY, diag, index, min_queries=6,
    )

    # Prompt field axes should all be satisfied
    pf_axes = [a for a in result["axes"] if a["axis_type"] == "prompt_field"]
    for a in pf_axes:
        assert a["sufficient"] is True, f"{a['axis']} not satisfied"

    # Pipeline param axes are never satisfied by index
    pp_axes = [a for a in result["axes"] if a["axis_type"] == "pipeline_param"]
    for a in pp_axes:
        assert a["sufficient"] is False

    # all_satisfied is False because of pipeline params
    assert result["summary"]["all_satisfied"] is False
    assert result["summary"]["prompt_field_axes_satisfied"] == len(pf_axes)


def test_min_queries_threshold():

    baseline = _make_baseline()
    diag = _diagnostic(6)
    partial_queries = [d["query"] for d in diag[:3]]

    rendered = baseline.derive_candidate(persona="You are a domain expert.").render()
    index = _build_index({rendered: partial_queries})

    # min_queries=6 -> not usable
    result_strict = assess_scan_coverage(
        baseline, VARIANT_LIBRARY, diag, index, min_queries=6,
    )
    persona_axis = next(a for a in result_strict["axes"] if a["axis"] == "persona")
    expert_variant = next(
        v for v in persona_axis["variants"] if "domain" in v["value_preview"]
    )
    assert expert_variant["n_cached"] == 3
    assert expert_variant["usable"] is False

    # min_queries=3 -> usable
    result_lenient = assess_scan_coverage(
        baseline, VARIANT_LIBRARY, diag, index, min_queries=3,
    )
    persona_axis = next(a for a in result_lenient["axes"] if a["axis"] == "persona")
    expert_variant = next(
        v for v in persona_axis["variants"] if "domain" in v["value_preview"]
    )
    assert expert_variant["usable"] is True


def test_axis_requirements_partial():

    baseline = _make_baseline()
    diag = _diagnostic(6)
    query_strings = [d["query"] for d in diag]

    # Cover 2 of 3 persona variants
    entries: dict[str, list[str]] = {}
    for val in ["You are a domain expert.", "You are a precise system."]:
        rendered = baseline.derive_candidate(persona=val).render()
        entries[rendered] = query_strings

    index = _build_index(entries)
    result = assess_scan_coverage(
        baseline, VARIANT_LIBRARY, diag, index,
        min_queries=6,
        axis_requirements={"persona": 2},
    )
    persona_axis = next(a for a in result["axes"] if a["axis"] == "persona")
    assert persona_axis["n_usable"] == 2
    assert persona_axis["n_required"] == 2
    assert persona_axis["sufficient"] is True


def test_sufficient_axes_excluded_from_needed():

    baseline = _make_baseline()
    diag = _diagnostic(6)
    query_strings = [d["query"] for d in diag]

    # Give task_intent full coverage (2 variants × 6 queries each)
    entries: dict[str, list[str]] = {}
    for ti_val in ["Identify the best match.", "Rank candidates by relevance."]:
        rendered = baseline.derive_candidate(task_intent=ti_val).render()
        entries[rendered] = query_strings
    index = _build_index(entries)

    # No pipeline params — isolate prompt-field logic
    lib_no_pp = {k: v for k, v in VARIANT_LIBRARY.items() if k != "pipeline_params"}

    # At min_queries=6: task_intent sufficient, persona not
    result = assess_scan_coverage(
        baseline, lib_no_pp, diag, index, min_queries=6,
    )
    ti_axis = next(a for a in result["axes"] if a["axis"] == "task_intent")
    persona_axis = next(a for a in result["axes"] if a["axis"] == "persona")
    assert ti_axis["sufficient"] is True
    assert persona_axis["sufficient"] is False

    # "needed" should only include persona's uncached calls (3 variants × 6 = 18)
    assert result["summary"]["backend_calls_needed"] == 3 * 6

    # "saved" should include task_intent's cached queries (2 × 6 = 12)
    assert result["summary"]["backend_calls_saved"] == 2 * 6


def test_pipeline_param_historical_discovery():

    baseline = _make_baseline()
    diag = _diagnostic(6)
    query_strings = [d["query"] for d in diag]

    # Put baseline results into the index with terminated_at annotations
    bl_rendered = baseline.render()
    bl_hash = _rp_hash(bl_rendered)
    index = {
        bl_hash: {
            q: {
                "query": q,
                "hit": True,
                "pipeline_data": {"terminated_at": "llm_ranking"},
            }
            for q in query_strings
        },
    }

    result = assess_scan_coverage(
        baseline, VARIANT_LIBRARY, diag, index,
        pipeline_params={"steps": ["token_matching", "llm_ranking"]},
        min_queries=6,
    )

    pp_axes = [a for a in result["axes"] if a["axis_type"] == "pipeline_param"]
    assert len(pp_axes) == 1
    pp = pp_axes[0]

    # All 6 diagnostic queries found in index
    assert pp["n_cached_historical"] == 6
    # All terminated at llm_ranking which is in target steps
    assert pp["n_step_compatible"] == 6
    assert "6 historical results found" in pp["note"]
    assert "6 step-compatible" in pp["note"]
    # Still not "sufficient" — pipeline params always need fresh eval
    assert pp["sufficient"] is False


def test_pipeline_param_no_historical_data():

    baseline = _make_baseline()
    diag = _diagnostic(6)

    result = assess_scan_coverage(
        baseline, VARIANT_LIBRARY, diag, {},
        pipeline_params={"steps": ["token_matching", "llm_ranking"]},
        min_queries=6,
    )

    pp_axes = [a for a in result["axes"] if a["axis_type"] == "pipeline_param"]
    assert len(pp_axes) == 1
    pp = pp_axes[0]
    assert pp["n_cached_historical"] == 0
    assert pp["n_step_compatible"] == 0
    assert "No historical data" in pp["note"]


def test_pipeline_param_partial_compatibility():

    baseline = _make_baseline()
    diag = _diagnostic(6)
    query_strings = [d["query"] for d in diag]

    bl_hash = _rp_hash(baseline.render())
    items = {}
    for i, q in enumerate(query_strings):
        # First 4 terminated at llm_ranking (compatible), last 2 at web_search (not)
        terminated = "llm_ranking" if i < 4 else "web_search"
        items[q] = {
            "query": q, "hit": True,
            "pipeline_data": {"terminated_at": terminated},
        }
    index = {bl_hash: items}

    result = assess_scan_coverage(
        baseline, VARIANT_LIBRARY, diag, index,
        pipeline_params={"steps": ["token_matching", "llm_ranking"]},
        min_queries=6,
    )

    pp = next(a for a in result["axes"] if a["axis_type"] == "pipeline_param")
    assert pp["n_cached_historical"] == 6
    assert pp["n_step_compatible"] == 4
