"""L1 phase — generate → score → execute round.

The round-loop orchestrator. Public surface:

* :func:`execute_round` — top-level round driver (called by ``runner``)
* :func:`generate_or_load_candidates` — fresh-generate or replay-from-disk
* :func:`l1_generate` — LLM meta-prompt call producing candidate variants
* :func:`l1_score` — per-round scoring + winner selection
* :func:`score_population` — per-population scoring loop

Out-of-bounds: no module here writes campaign artifacts directly
(persistence routes through ``CycleEventLog.append``); LLM calls only
through ``l1_generate`` / ``l1_critique`` paths; no dispatch-hub bypass
for prompt fills.
"""

from __future__ import annotations

from promptpotter.application.optimization.l1.execute import execute_round
from promptpotter.application.optimization.l1.generate import (
    candidate_summaries,
    l1_generate,
)
from promptpotter.application.optimization.l1.resume import generate_or_load_candidates
from promptpotter.application.optimization.l1.score import (
    CandidateOutcome,
    CandidateRunResult,
    SignalEffect,
    decode_signal_effect,
    l1_score,
    score_one_candidate,
    score_population,
)

__all__ = [
    "CandidateOutcome",
    "CandidateRunResult",
    "SignalEffect",
    "candidate_summaries",
    "decode_signal_effect",
    "execute_round",
    "generate_or_load_candidates",
    "l1_generate",
    "l1_score",
    "score_one_candidate",
    "score_population",
]
