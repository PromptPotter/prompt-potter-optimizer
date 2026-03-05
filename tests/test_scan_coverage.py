"""Tests for assess_scan_coverage()."""

from api.services.search import assess_scan_coverage

from _helpers import rp_hash as _rp_hash, make_baseline_ps as _make_baseline


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
    """All prompt-field variants have full query coverage -> all prompt axes satisfied.

    Pipeline-param axes are never satisfied by index, so all_satisfied stays False.
    """
    baseline = _make_baseline()
    diag = _diagnostic(6)
    query_strings = [d["query"] for d in diag]

    # Build index with full coverage for baseline + all persona/task_intent variants
    entries: dict[str, list[str]] = {}
    entries[baseline.render()] = query_strings

    for persona_val in ["You are a domain expert.", "You are a precise system.",
                        "You are a careful assistant."]:
        rendered = baseline.derive(persona=persona_val).render()
        entries[rendered] = query_strings

    for ti_val in ["Identify the best match.", "Rank candidates by relevance."]:
        rendered = baseline.derive(task_intent=ti_val).render()
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
    """Variant with 3/6 queries: not usable at min_queries=6, usable at min_queries=3."""
    baseline = _make_baseline()
    diag = _diagnostic(6)
    partial_queries = [d["query"] for d in diag[:3]]

    rendered = baseline.derive(persona="You are a domain expert.").render()
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
    """Require 2 of 3 persona values, have 2 -> satisfied."""
    baseline = _make_baseline()
    diag = _diagnostic(6)
    query_strings = [d["query"] for d in diag]

    # Cover 2 of 3 persona variants
    entries: dict[str, list[str]] = {}
    for val in ["You are a domain expert.", "You are a precise system."]:
        rendered = baseline.derive(persona=val).render()
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
    """When an axis becomes sufficient, its uncached calls drop from needed."""
    baseline = _make_baseline()
    diag = _diagnostic(6)
    query_strings = [d["query"] for d in diag]

    # Give task_intent full coverage (2 variants × 6 queries each)
    entries: dict[str, list[str]] = {}
    for ti_val in ["Identify the best match.", "Rank candidates by relevance."]:
        rendered = baseline.derive(task_intent=ti_val).render()
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
