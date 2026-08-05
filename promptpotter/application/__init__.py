"""application/ — orchestration: the use-case layer over domain/, under infrastructure/.

initialization/ — init_services / init_optimization_loop (wiring → Session).
optimization/ — the L1/L2/L3 loop: Cycle, dispatch hub, escalation, PoBB,
  resume_and_fork (its CLAUDE.md is the agent contract).
intelligence/ — materialized views over the MeasurementArchive (AxisIndex,
  SampleIndex, Rasch); MUST NOT import optimization/.
scoring/ — the score_search_point() gateway + formula / evaluators / metrics.
sweep/ — cheap A/B sibling runs ahead of full promotion.
runner/ — master orchestrator + optimize-loop entry point.

full contract: application/CLAUDE.md
"""
