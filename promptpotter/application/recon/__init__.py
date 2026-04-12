"""Optional human-driven reconnaissance package.

The recon pass (a one-at-a-time sensitivity scan) is an *optional* human-in-the-loop
exploration step that runs before the AI optimization loop. It probes which axes
of the search space actually move the needle, so the operator can focus the optimizer.

This package is independent of the optimization loop. The only sanctioned
bridge is ``ReconBrief`` flowing through ``RunConfig`` into L1 — see
``application/campaign/config.py``.

Directionality: ``recon`` may import from ``optimization`` (for primitives like
``llm_call``), but ``optimization`` must never import from ``recon``.
"""

from __future__ import annotations

from promptpotter.application.recon.adaptive_recon import (
    build_diagnostic_set,
    load_filtered_variant_library,
    run_adaptive_recon,
)
from promptpotter.application.recon.coverage import (
    CoverageEstimate,
    estimate_recon_coverage,
)
from promptpotter.application.recon.recon_advisor import (
    advise_recon,
    build_llm_context,
    build_pipeline_overview,
    build_tunable_params,
    convert_advisory_to_recon_variants,
    preview_advisor_prompt,
)
from promptpotter.application.recon.recon_report import (
    decompose_recon_baseline,
    finalize_scan,
    prepare_recon_brief,
    resume_or_build_diagnostic,
)
from promptpotter.application.recon.recon_runner import run_recon

__all__ = [
    "CoverageEstimate",
    "advise_recon",
    "build_diagnostic_set",
    "build_llm_context",
    "build_pipeline_overview",
    "build_tunable_params",
    "convert_advisory_to_recon_variants",
    "decompose_recon_baseline",
    "estimate_recon_coverage",
    "finalize_scan",
    "load_filtered_variant_library",
    "prepare_recon_brief",
    "preview_advisor_prompt",
    "resume_or_build_diagnostic",
    "run_adaptive_recon",
    "run_recon",
]
