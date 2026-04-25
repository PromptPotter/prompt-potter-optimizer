"""DORMANT — do not maintain. See ./README.md.

Sensitivity-scan / recon package, preserved as a working-shape reference.
Has zero callers in the active loop. Read-only by policy; do not refactor,
"improve," modernize, or fix this code. If a test breaks, skip it. If a
lint or type rule fails, exclude the file.

Directionality (when revived): ``recon`` may import from ``optimization``
(for primitives like ``llm_call``); ``optimization`` must never import
from ``recon``.
"""

from __future__ import annotations

from promptpotter.application.recon.adaptive_recon import (
    build_diagnostic_set,
    filter_variant_library,
    run_adaptive_recon,
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
    "advise_recon",
    "build_diagnostic_set",
    "build_llm_context",
    "build_pipeline_overview",
    "build_tunable_params",
    "convert_advisory_to_recon_variants",
    "decompose_recon_baseline",
    "filter_variant_library",
    "finalize_scan",
    "prepare_recon_brief",
    "preview_advisor_prompt",
    "resume_or_build_diagnostic",
    "run_adaptive_recon",
    "run_recon",
]
