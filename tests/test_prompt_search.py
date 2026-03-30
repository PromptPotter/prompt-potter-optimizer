"""Tests for smart prompt search: diagnostic set, axis classification, variant library."""
import pytest

from api.config.settings import load_variant_library
from api.models.opt_search_point import OptSearchPoint
from api.models.search_point import JobSearchPoint
from api.services.prompt_eval import _error_category, _most_common_error_category
from api.services.search.sensitivity_scan import sensitivity_scan
from api.services.search.smart_search import _profiles_from_rows


def _make_eval_data(n: int) -> list:
    return [
        {"query": f"query_{i}", "ground_truth": f"gt_{i}", "pipeline_data": {}}
        for i in range(n)
    ]


def _make_scan_baseline(
    instruction: str = "test",
    persona: str = "default_persona",
    pipeline_params: dict | None = None,
    **kwargs,
) -> tuple[JobSearchPoint, OptSearchPoint]:
    """Build a (JobSearchPoint, OptSearchPoint) pair for scan tests."""
    osp = OptSearchPoint(instruction=instruction, persona=persona, **kwargs)
    pp = pipeline_params or {"steps": ["llm_ranking"]}
    jsp = osp.to_job_search_point(base_pipeline_params=pp)
    return jsp, osp


@pytest.fixture
def scan_eval_mock(monkeypatch):
    """Fixture that returns a function to patch eval_search_point for scan tests."""
    import api.services.search.sensitivity_scan as _scan

    def _apply(mock_eval):
        monkeypatch.setattr(_scan, "eval_search_point", mock_eval)

    return _apply


def test_load_variant_library():
    lib = load_variant_library()
    assert "prompt_fields" in lib
    assert "persona" in lib["prompt_fields"]
    assert len(lib["prompt_fields"]["persona"]) > 1


def test_profiles_from_rows():
    axes = [
        ("persona", "prompt_field", ["a", "b", "c"]),
        ("temp", "pipeline_param", ["0.1", "0.9"]),
    ]
    rows = [
        {"axis": "persona", "delta": 0.0},
        {"axis": "persona", "delta": 0.1},
        {"axis": "persona", "delta": -0.05},
        {"axis": "temp", "delta": 0.0},
        {"axis": "temp", "delta": 0.2},
    ]
    profiles = _profiles_from_rows(rows, axes, n_eval=6)
    assert len(profiles) == 2
    assert profiles[0]["axis"] == "temp"  # range 0.2
    assert profiles[1]["axis"] == "persona"  # range 0.15
    assert profiles[0]["sensitivity_range"] == 0.2
    assert profiles[1]["best_delta"] == 0.1


@pytest.mark.asyncio
async def test_scan_rows_include_errors(scan_eval_mock):
    sp, osp = _make_scan_baseline()
    baseline_rendered = sp.render()

    scan_variants = {"persona": ["default_persona", "expert"]}
    eval_data = _make_eval_data(4)

    async def _mock_eval(search_point, data, client, **kw):
        if search_point.render() != baseline_rendered:
            return (
                [{"hit": False, "query": d["query"]} for d in data],
                {"accuracy": 0.0, "hits": 0, "total": 4, "errors": 2},
                False,
            )
        return (
            [{"hit": True, "query": d["query"]} for d in data],
            {"accuracy": 1.0, "hits": 4, "total": 4},
            False,
        )

    scan_eval_mock(_mock_eval)

    df, _profiles = await sensitivity_scan(
        sp, scan_variants, eval_data, backend_client=None,
        baseline_opt=osp,
    )

    # Both baseline and perturbed rows should have 'errors' field
    assert "errors" in df.columns
    baseline_row = df[df["value_preview"] == "default_persona"].iloc[0]
    assert baseline_row["errors"] == 0
    perturbed_row = df[df["value_preview"] == "expert"].iloc[0]
    assert perturbed_row["errors"] == 2


@pytest.mark.asyncio
async def test_scan_aborts_on_baseline_all_errors(scan_eval_mock):
    sp, osp = _make_scan_baseline()
    scan_variants = {"persona": ["default_persona", "expert", "analyst"]}
    eval_data = _make_eval_data(4)

    call_count = 0

    async def _mock_eval(search_point, data, client, **kw):
        nonlocal call_count
        call_count += 1
        return (
            [{"hit": False, "query": d["query"]} for d in data],
            {"accuracy": 0.0, "hits": 0, "total": 4, "errors": 4},
            False,
        )

    scan_eval_mock(_mock_eval)

    df, profiles = await sensitivity_scan(
        sp, scan_variants, eval_data, backend_client=None,
        baseline_opt=osp,
    )
    assert df.empty
    assert profiles == []
    assert call_count == 1  # only baseline eval, no variants


@pytest.mark.asyncio
async def test_scan_aborts_after_consecutive_all_error_variants(scan_eval_mock):
    sp, osp = _make_scan_baseline()
    baseline_rendered = sp.render()
    scan_variants = {"persona": ["default_persona", "expert", "analyst", "reviewer"]}
    eval_data = _make_eval_data(4)

    call_count = 0

    async def _mock_eval(search_point, data, client, **kw):
        nonlocal call_count
        call_count += 1
        if search_point.render() == baseline_rendered:
            # Baseline succeeds
            return (
                [{"hit": True, "query": d["query"]} for d in data],
                {"accuracy": 1.0, "hits": 4, "total": 4, "errors": 0},
                False,
            )
        # All variants error out
        return (
            [{"hit": False, "query": d["query"]} for d in data],
            {"accuracy": 0.0, "hits": 0, "total": 4, "errors": 4},
            False,
        )

    scan_eval_mock(_mock_eval)

    df, _profiles = await sensitivity_scan(
        sp, scan_variants, eval_data, backend_client=None,
        baseline_opt=osp,
    )
    # 1 baseline + 2 variants (circuit breaker fires on 2nd all-error)
    assert call_count == 3
    # Partial results still present
    assert len(df) > 0




# ---------------------------------------------------------------------------
# Error classification tests
# ---------------------------------------------------------------------------

class TestErrorCategory:
    @pytest.mark.parametrize("input_str,expected", [
        ("[CLIENT] HTTP 400: bad request", "CLIENT"),
        ("[SERVER] HTTP 500: internal", "SERVER"),
        ("[CONNECTION] timeout", "CONNECTION"),
        ("some old-style error", None),
        (None, None),
        ("", None),
    ])
    def test_classify(self, input_str, expected):
        assert _error_category(input_str) == expected


class TestDominantErrorCategory:
    @pytest.mark.parametrize("errors,expected", [
        (["[CLIENT] HTTP 400: bad", "[CLIENT] HTTP 400: bad", "skipped_after_client_error"], "CLIENT"),
        (["[CONNECTION] timeout", "[CONNECTION] timeout", "[SERVER] HTTP 500: err"], "CONNECTION"),
        (["skipped_after_consecutive_errors", "skipped_after_consecutive_errors"], None),
        ([None, None], None),
    ])
    def test_classify(self, errors, expected):
        results = [{"error": e} for e in errors]
        assert _most_common_error_category(results) == expected


@pytest.mark.asyncio
async def test_scan_baseline_client_error_message(scan_eval_mock):
    """Baseline all-CLIENT-errors should mention config, not 'backend down'."""
    sp, osp = _make_scan_baseline()
    scan_variants = {"persona": ["default_persona", "expert"]}
    eval_data = _make_eval_data(4)

    events: list[dict] = []

    async def _mock_eval(search_point, data, client, **kw):
        return (
            [{"hit": False, "query": d["query"],
              "error": "[CLIENT] HTTP 400: bad"} for d in data],
            {"accuracy": 0.0, "hits": 0, "total": 4, "errors": 4},
            False,
        )

    scan_eval_mock(_mock_eval)

    df, _profiles = await sensitivity_scan(
        sp, scan_variants, eval_data, backend_client=None,
        baseline_opt=osp,
        progress_cb=events.append,
    )
    assert df.empty
    abort_events = [e for e in events if e.get("type") == "scan_aborted"]
    assert len(abort_events) == 1
    reason = abort_events[0]["reason"]
    assert "client errors" in reason.lower()
    assert "backend may be down" not in reason.lower()


@pytest.mark.asyncio
async def test_batch_fast_abort_on_client_error(monkeypatch):
    """_run_eval_batch should abort after first CLIENT error."""
    import api.services.prompt_eval as _pe

    call_count = 0

    async def _counting_eval(query_data, backend_client, rendered, **kw):
        nonlocal call_count
        call_count += 1
        return {
            "query": query_data["query"],
            "predicted": "ERROR",
            "ground_truth": query_data.get("ground_truth", ""),
            "hit": False,
            "score": 0.0,
            "error": "[CLIENT] HTTP 400: Bad Request",
            "pipeline_data": None,
        }

    monkeypatch.setattr(_pe, "eval_query_via_backend", _counting_eval)

    sp = JobSearchPoint(
        pipeline_params={"llm_ranking": {"prompt": "test"}},
    )
    eval_data = _make_eval_data(10)

    batch = await _pe._run_eval_batch(sp, eval_data, backend_client=None)
    results = batch.results
    # Only 1 actual backend call — the rest are skipped
    assert call_count == 1
    assert len(results) == 10
    assert results[0]["error"] == "[CLIENT] HTTP 400: Bad Request"
    assert all(
        r["error"] == "skipped_after_client_error"
        for r in results[1:]
    )


@pytest.mark.asyncio
async def test_batch_server_errors_use_consecutive_threshold(monkeypatch):
    """Server errors should still require 3 consecutive failures before abort."""
    import api.services.prompt_eval as _pe

    call_count = 0

    async def _server_error_eval(query_data, backend_client, rendered, **kw):
        nonlocal call_count
        call_count += 1
        return {
            "query": query_data["query"],
            "predicted": "ERROR",
            "ground_truth": query_data.get("ground_truth", ""),
            "hit": False,
            "score": 0.0,
            "error": "[SERVER] HTTP 500: Internal Server Error",
            "pipeline_data": None,
        }

    monkeypatch.setattr(_pe, "eval_query_via_backend", _server_error_eval)

    sp = JobSearchPoint(
        pipeline_params={"llm_ranking": {"prompt": "test"}},
    )
    eval_data = _make_eval_data(10)

    batch = await _pe._run_eval_batch(sp, eval_data, backend_client=None)
    results = batch.results
    # 3 actual calls (consecutive threshold), then 7 skipped
    assert call_count == 3
    assert len(results) == 10
    skipped = [r for r in results if r["error"] == "skipped_after_consecutive_errors"]
    assert len(skipped) == 7
