"""L1 phase — the per-round generate → critique → score → execute loop.

The innermost optimizer layer; read this file to orient on which module
owns which beat of a round. Agent contract (what L1 may mutate, with
cause) lives in ``../CLAUDE.md``.

CONCEPT MAP (round flow):
* **execute** (:mod:`.execute`) — :func:`execute_round`, the top-level
  round driver the ``runner`` calls; sequences critique → generate → score.
* **generate** (:mod:`.generate`) — :func:`l1_generate`, the LLM meta-prompt
  call producing N candidate variants; :func:`candidate_summaries`.
* **critique** (:mod:`.critique`) — ``run_l1_critique``, the prior-round
  self-critique LLM call (output rides ``OptSearchPoint`` into next gen).
* **population** (:mod:`.population`) — projects raw proposals → scored
  ``OptSearchPoint``s + score reports (between generate and score).
* **score** (:mod:`.score` package) — :func:`l1_score` (per-round scoring +
  winner pick), :func:`score_population`, :func:`score_one_candidate`,
  ``SignalEffect`` / :func:`decode_signal_effect` (escalation-signal decode).
* **resume** (:mod:`.resume`) — :func:`generate_or_load_candidates`:
  fresh-generate or replay-from-disk on resume.

Also: ``stats.py`` (``L1Stats`` per-cycle aggregation; not re-exported).

Out-of-bounds: no module writes campaign artifacts directly (persistence
routes through ``CycleEventLog.append``); LLM calls only via the generate /
critique paths; no dispatch-hub bypass for prompt fills.
"""
