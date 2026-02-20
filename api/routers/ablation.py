"""
Ablation comparison endpoints.

Stores replay results and serves statistical comparisons via the API.
Data is persisted as JSON files in .promptpotter/ablation/.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/ablation", tags=["Ablation"])

# Storage directory
DATA_DIR = Path(".promptpotter") / "ablation"


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class AblationResultItem(BaseModel):
    """One query's result from an ablation replay."""

    query: str
    bom_material: Optional[str] = None
    process: Optional[str] = None
    ground_truth: str
    predicted: str
    confidence: float = 0.0
    ranked_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    latency_ms: float = 0.0
    web_search_status: Optional[str] = None
    status: str = "success"
    # Original variant data for comparison
    variant_b_predicted: Optional[str] = None
    variant_b_latency_ms: Optional[float] = None
    variant_b_confidence: Optional[float] = None


class IngestRequest(BaseModel):
    """Request to ingest ablation replay results."""

    variant_label: str = Field(
        ..., description="Human-readable label, e.g. 'LLM1-TokenMatching (no LLM2)'"
    )
    pipeline_notation: str = Field(
        ..., description="Pipeline notation, e.g. 'LLM1-TokenMatching'"
    )
    source_experiment: str = Field(
        ..., description="ID of the source experiment"
    )
    source_run_id: Optional[str] = None
    session_terms_count: Optional[int] = None
    limitations: List[str] = Field(default_factory=list)
    results: List[AblationResultItem]


class IngestResponse(BaseModel):
    """Response after ingesting results."""

    ablation_id: str
    query_count: int
    successful_count: int
    stored_at: str


class ComparisonSummary(BaseModel):
    """Summary returned by the compare endpoint."""

    ablation_id: str
    pipeline: Dict[str, Any]
    dataset: Dict[str, Any]
    metrics: Dict[str, Any]
    classification: Dict[str, Any]
    limitations: List[str]


class AblationListItem(BaseModel):
    """Item in the list of stored ablation results."""

    ablation_id: str
    variant_label: str
    pipeline_notation: str
    source_experiment: str
    query_count: int
    created_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def _save(ablation_id: str, data: dict) -> Path:
    path = _ensure_dir() / f"{ablation_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def _load(ablation_id: str) -> dict:
    path = DATA_DIR / f"{ablation_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Ablation {ablation_id} not found")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _hit_at_k(ranked: List[Dict], ground_truth: str, k: int) -> bool:
    return any(c.get("candidate") == ground_truth for c in ranked[:k])


def _reciprocal_rank(ranked: List[Dict], ground_truth: str) -> float:
    for i, c in enumerate(ranked):
        if c.get("candidate") == ground_truth:
            return 1.0 / (i + 1)
    return 0.0


def _compute_comparison(data: dict) -> dict:
    """Compute metrics and classification from stored ablation data."""
    import statistics as stats

    results = [r for r in data["results"] if r.get("status") == "success"]
    n = len(results)
    if n == 0:
        raise HTTPException(status_code=400, detail="No successful results to compare")

    a_correct = []
    b_correct = []
    a_latencies = []
    b_latencies = []
    a_hit3 = a_hit5 = 0
    a_rr_sum = 0.0

    classification = {
        "both_correct": [],
        "a_only_correct": [],
        "b_only_correct": [],
        "both_wrong": [],
    }

    for r in results:
        gt = r["ground_truth"]
        a_pred = r.get("predicted", "")
        b_pred = r.get("variant_b_predicted", "")
        ranked = r.get("ranked_candidates", [])

        a_ok = a_pred == gt
        b_ok = b_pred == gt
        a_correct.append(a_ok)
        b_correct.append(b_ok)
        a_latencies.append(r.get("latency_ms", 0))
        b_latencies.append(r.get("variant_b_latency_ms", 0))

        if _hit_at_k(ranked, gt, 3):
            a_hit3 += 1
        if _hit_at_k(ranked, gt, 5):
            a_hit5 += 1
        a_rr_sum += _reciprocal_rank(ranked, gt)

        summary = {
            "query": r["query"],
            "ground_truth": gt,
            "a_predicted": a_pred,
            "b_predicted": b_pred,
        }
        if a_ok and b_ok:
            classification["both_correct"].append(summary)
        elif a_ok:
            classification["a_only_correct"].append(summary)
        elif b_ok:
            classification["b_only_correct"].append(summary)
        else:
            classification["both_wrong"].append(summary)

    # McNemar's test
    n01 = sum(1 for a, b in zip(a_correct, b_correct) if not a and b)
    n10 = sum(1 for a, b in zip(a_correct, b_correct) if a and not b)
    mcnemar = {"n_a_only": n10, "n_b_only": n01}
    if n01 + n10 > 0:
        try:
            from scipy.stats import chi2

            stat = (abs(n01 - n10) - 1) ** 2 / (n01 + n10)
            mcnemar["statistic"] = round(stat, 4)
            mcnemar["p_value"] = round(1 - chi2.cdf(stat, df=1), 4)
        except ImportError:
            mcnemar["p_value"] = None
            mcnemar["note"] = "scipy not installed"
    else:
        mcnemar["p_value"] = 1.0
        mcnemar["note"] = "No discordant pairs"

    # Wilcoxon test
    wilcoxon = {}
    try:
        from scipy.stats import wilcoxon as wilcoxon_test

        stat, p = wilcoxon_test(a_latencies, b_latencies)
        wilcoxon = {"statistic": round(float(stat), 4), "p_value": round(float(p), 4)}
    except ImportError:
        wilcoxon = {"p_value": None, "note": "scipy not installed"}
    except ValueError as e:
        wilcoxon = {"p_value": None, "note": str(e)}

    def lat_stats(lats):
        s = sorted(lats)
        p95_idx = int(len(s) * 0.95)
        return {
            "mean": round(stats.mean(s), 1),
            "median": round(stats.median(s), 1),
            "p95": round(s[min(p95_idx, len(s) - 1)], 1),
        }

    a_hit1 = sum(a_correct)
    b_hit1 = sum(b_correct)

    return {
        "pipeline": {
            "variant_a": {
                "notation": data.get("pipeline_notation", ""),
                "label": data.get("variant_label", ""),
            },
            "variant_b": {
                "notation": "LLM1-TokenMatching-LLM2",
                "label": "Full pipeline",
            },
        },
        "dataset": {"query_count": n, "session_terms": data.get("session_terms_count")},
        "metrics": {
            "hit_at_1": {
                "a": round(a_hit1 / n, 4),
                "a_count": a_hit1,
                "b": round(b_hit1 / n, 4),
                "b_count": b_hit1,
                "mcnemar": mcnemar,
            },
            "hit_at_3": {"a": round(a_hit3 / n, 4), "a_count": a_hit3, "b": None},
            "hit_at_5": {"a": round(a_hit5 / n, 4), "a_count": a_hit5, "b": None},
            "mrr": {"a": round(a_rr_sum / n, 4), "b": None},
            "latency_ms": {
                "a": lat_stats(a_latencies),
                "b": lat_stats(b_latencies),
                "wilcoxon": wilcoxon,
            },
        },
        "classification": {
            "both_correct": len(classification["both_correct"]),
            "a_only_correct": len(classification["a_only_correct"]),
            "b_only_correct": len(classification["b_only_correct"]),
            "both_wrong": len(classification["both_wrong"]),
            "details": classification,
        },
        "limitations": data.get("limitations", []),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/results", response_model=IngestResponse)
async def ingest_results(request: IngestRequest):
    """Ingest ablation replay results into PromptPotter's storage."""
    ablation_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()

    data = {
        "ablation_id": ablation_id,
        "variant_label": request.variant_label,
        "pipeline_notation": request.pipeline_notation,
        "source_experiment": request.source_experiment,
        "source_run_id": request.source_run_id,
        "session_terms_count": request.session_terms_count,
        "limitations": request.limitations,
        "created_at": now,
        "query_count": len(request.results),
        "successful_count": sum(1 for r in request.results if r.status == "success"),
        "results": [r.model_dump() for r in request.results],
    }

    _save(ablation_id, data)

    return IngestResponse(
        ablation_id=ablation_id,
        query_count=data["query_count"],
        successful_count=data["successful_count"],
        stored_at=now,
    )


@router.get("/results", response_model=List[AblationListItem])
async def list_results():
    """List all stored ablation results."""
    _ensure_dir()
    items = []
    for path in sorted(DATA_DIR.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        items.append(
            AblationListItem(
                ablation_id=data.get("ablation_id", path.stem),
                variant_label=data.get("variant_label", ""),
                pipeline_notation=data.get("pipeline_notation", ""),
                source_experiment=data.get("source_experiment", ""),
                query_count=data.get("query_count", 0),
                created_at=data.get("created_at", ""),
            )
        )
    return items


@router.get("/results/{ablation_id}")
async def get_results(ablation_id: str):
    """Get full ablation results by ID."""
    return _load(ablation_id)


@router.get("/compare/{ablation_id}", response_model=ComparisonSummary)
async def compare(ablation_id: str):
    """Compute and return statistical comparison for an ablation result set."""
    data = _load(ablation_id)
    comparison = _compute_comparison(data)
    return ComparisonSummary(
        ablation_id=ablation_id,
        **comparison,
    )
