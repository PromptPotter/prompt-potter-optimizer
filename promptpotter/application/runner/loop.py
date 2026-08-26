"""Round loop — generate → score → escalate → stop. Pause and budget are polled EVERY clean round, so ``pause-cycle``
exits resumably and ``change-spend-budget`` moves a ceiling mid-flight without a restart."""

from __future__ import annotations

import logging
import traceback

from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.dispatch.facade import (
    InjectionRenderError,
    MandatoryPanelStarvedError,
)
from promptpotter.application.optimization.l1.execute import execute_round
from promptpotter.application.run_observers import RunCallbacks
from promptpotter.application.run_phase_control import declare_run_phase, pause_requested
from promptpotter.application.runner.generation_only import run_generation_only_round
from promptpotter.application.runner.origin_gate import run_origin_gate
from promptpotter.application.runner.round import (
    close_round,
    emit_origin_round,
    escalate_or_stop,
    persist_round,
    post_round,
)
from promptpotter.application.runner.termination import (
    BudgetGate,
    backend_unreachable_tripped,
    origin_gate_tripped,
)
from promptpotter.domain.phases import (
    CampaignPhase,
    RunPhase,
    StopLoop,
    StopReason,
    emit_phase,
)
from promptpotter.domain.run_records import ErrorRecord, PhaseRecord
from promptpotter.domain.sample import Sample
from promptpotter.infrastructure.llm.telemetry import emit_error_record

logger = logging.getLogger(__name__)


HARD_CAP: int = 100  # runaway-loop guard for max_rounds=None + non-converging L2/L3


async def run_round_loop(
    cycle: Cycle,
    dataset: list[Sample],
    config: CampaignConfig,
    session: Session,
    cb: RunCallbacks,
    *,
    sweep: bool = False,
    diag: bool = False,
    halt_at_accuracy: float | None = None,
    stop_after_rounds: int | None = None,
    budget_gate: BudgetGate,
) -> tuple[StopReason, ErrorRecord | None]:
    """The round loop. The budget gate re-reads its caps every clean round, so ``change-spend-budget`` mutates a ceiling
    mid-flight. Returns ``(stop_reason, error)`` — ``error`` is set so the caller need not re-read the ledger."""
    opt = config.optimization
    # resumed_from_round = next L1 round (fresh=1); clean_rounds = lifetime L1 completed (origin not counted).
    round_num = session.state.resumed_from_round
    clean_rounds = max(session.state.resumed_from_round - 1, 0)
    # None ⇒ unlimited; HARD_CAP is the real ceiling either way.
    max_rounds = opt.max_rounds if opt.max_rounds is not None else HARD_CAP
    # `step-cycle`: advance exactly this many rounds in place then auto-pause (stays
    # resumable, so the operator can step again). Bounded by rounds completed THIS
    # invocation (delta off `clean_rounds`), reusing the pause stop below rather than
    # the configured ceiling.
    clean_rounds_at_start = clean_rounds

    try:
        # Origin is round 0 — emit it through the standard completion path before
        # the L1 loop on a fresh start (clean_rounds == 0) when it isn't already on
        # disk. Resume (round 0 present) and divergence/sweep forks (clean_rounds >
        # 0, round 0 inherited from the parent lane) skip it.
        if not sweep and not diag and clean_rounds == 0:
            round0_present = bool(
                session.state.cycle_id and session.store.campaigns.load_round_file(session.hop, 0)
            )
            if not round0_present:
                await emit_origin_round(cycle, session, cb)
                # Origin gate: a non-healthy round-0 verdict holds at an interactive
                # checkpoint before L1 instead of burning a campaign against a broken
                # floor (the common case while a dev brings up a new connector). The
                # operator decides — rescore (re-measure force-fresh after a backend
                # fix) / proceed (override) / abort — across webapp + CLI + notebook.
                # ``None`` ⇒ proceed into L1; a StopReason ⇒ end the cycle.
                if origin_gate_tripped(cycle.origin_round.health, opt.origin_gate) is not None:
                    gate_stop = await run_origin_gate(
                        cycle, dataset, config, session, cb, opt.origin_gate
                    )
                    if gate_stop is not None:
                        return gate_stop, None

        while clean_rounds < max_rounds and round_num < HARD_CAP:
            # Pause cooperation: exit cleanly at the round boundary when the
            # operator set the pause flag. The per-sample loop (run_query_loop)
            # checks the same predicate, so a mid-round pause lands within one
            # sample; this boundary check covers the single-LLM-call phases
            # (generate / L2 / L3) that have no inner loop. The cycle stays
            # resumable — `_finalize_run` skips terminal marking on PAUSED.
            if pause_requested(session):
                declare_run_phase(session, RunPhase.PAUSED)
                return StopReason.PAUSED, None

            # `step-cycle` boundary: once this invocation has advanced its allotted
            # rounds, auto-pause through the same resumable stop as an operator pause.
            if (
                stop_after_rounds is not None
                and clean_rounds - clean_rounds_at_start >= stop_after_rounds
            ):
                declare_run_phase(session, RunPhase.PAUSED)
                return StopReason.PAUSED, None

            # Full bank — execute_round's adaptive queue mechanism narrows it to sp_budget_ttest per round.
            round_scoring_data = session.scoring.scoring_set
            round_checks = session.scoring.degradation_checks

            logger.debug(
                "Round %d (clean=%d/%d, acc=%.3f, stall=%d/%d)",
                round_num,
                clean_rounds,
                max_rounds,
                cycle.tracking.current_accuracy,
                cycle.escalation.l1_stall_count,
                opt.l1_patience,
            )

            cb.set_round(round_num)
            ledger = session.state.ledger
            assert ledger is not None, (
                "build_run_observers must bind state.ledger before the round loop"
            )
            ledger.append(PhaseRecord(phase="round", event="enter", round=round_num))

            # The calendar cap's half of "no round will follow this one". The lives bank's
            # half can only be known after the round is scored, so `execute_round` /
            # `post_round` fold it in themselves via `EscalationFSM.would_exhaust_lives`.
            is_final_round = clean_rounds + 1 >= max_rounds

            # Sampled BEFORE the round is scored, because the warm now happens inside scoring
            # (`calibrate_ruler`, ahead of the election that needs it). Read after
            # `execute_round` this is already False on the round that warmed, and round 0 keeps
            # its cold θ on disk forever — the exact silence the re-persist below exists to break.
            ruler_was_cold = cycle.ruler is None
            round_result = await execute_round(
                cycle,
                round_num,
                round_scoring_data,
                cb,
                degradation_checks=round_checks,
                skip_critique=sweep,
                is_final_round=is_final_round,
            )
            # A cold ruler warms during the round, and the warm fit gives round 0 the θ it could
            # not have had at its own close. Round 0's document was written back then, so without
            # this the origin's ability lives only in memory: the file and the ledger keep the
            # cold value, and every non-live reader shows a θ-less C0 beside candidates that
            # have one.
            cycle.absorb_round(round_result, round_num)
            if ruler_was_cold and cycle.ruler is not None:
                persist_round(cycle, cycle.origin_round, session, cb)

            if cycle.axes and len(cycle.rounds) >= 2:
                cycle.axes.record_flips_from_rounds(cycle.rounds, round_num)

            if round_result.escalation_signal:
                signal = round_result.escalation_signal
                emit_phase(
                    cb.on_phase,
                    CampaignPhase.ESCALATION,
                    "enter",
                    round=round_num,
                    check_name=signal.check_name,
                    target=signal.target,
                    degraded_rate=signal.check_result.get("degraded_rate"),
                    warning_types=signal.check_result.get("warning_types"),
                )
                await close_round(cycle, round_result, round_num, session, cb)
                if backend_unreachable_tripped(round_result.health) is not None:
                    return StopReason.BACKEND_UNREACHABLE, None
                if session.state.cycle_id:
                    session.store.campaigns.delete_round_candidates(
                        session.hop,
                        round_num + 1,
                    )
                emit_phase(cb.on_phase, CampaignPhase.ESCALATION, "exit", round=round_num)
                round_num += 1
                continue

            await post_round(
                cycle,
                round_result,
                round_num,
                config,
                session,
                cb,
                is_final_round=is_final_round,
            )
            # A round that was mostly backend-down isn't a measurement — halt instead
            # of grinding more zero-accuracy rounds against a dead backend (the operator
            # restarts it and ``resume``s). Mid-run sibling of the round-0 origin gate.
            if backend_unreachable_tripped(round_result.health) is not None:
                return StopReason.BACKEND_UNREACHABLE, None
            round_num += 1
            clean_rounds += 1

            if halt_at_accuracy is not None and cycle.tracking.best_accuracy >= halt_at_accuracy:
                return StopReason.TARGET_HIT, None
            budget_stop = budget_gate.tripped()
            if budget_stop is not None:
                return budget_stop, None

            if sweep and clean_rounds >= 1:
                await run_generation_only_round(
                    cycle, session, cb, round_num, label="sweep_gen_only"
                )
                return StopReason.SWEEP_COMPLETE, None

            if diag and clean_rounds >= 1:
                # Force L2 (bypass stall counter) on R1 evidence; peek R2 with L2 overrides.
                await escalate_or_stop(cycle, config, session, round_num - 1, cb)
                await run_generation_only_round(
                    cycle, session, cb, round_num, label="diag_gen_only"
                )
                return StopReason.DIAG_COMPLETE, None

        return (StopReason.HARD_CAP if round_num >= HARD_CAP else StopReason.MAX_ROUNDS), None

    except StopLoop as sl:
        return sl.reason, None
    except KeyboardInterrupt as exc:
        # The PAUSE FLAG's stop (`scoring/search_point_scorer.py`), not the terminal's — a
        # Ctrl+C arrives as ``CancelledError`` and lands in `runner/entry.py`. Which is also why
        # one is not caught here: a cancellation is our own machinery, and must reach its asker.
        logger.warning(
            "Optimization paused at round %d (%s).", round_num, str(exc) or "user-initiated"
        )
        return StopReason.PAUSED, None
    except (InjectionRenderError, MandatoryPanelStarvedError) as exc:
        # The prompt could not be composed correctly — a renderer raised, or a panel the node
        # cannot operate without was not placed. Distinct from CRASHED so the operator can pinpoint
        # the composition rather than the search, and a HALT rather than a degraded prompt: a node
        # handed no subject still answers, confidently, and every instrument downstream reads green.
        tb = traceback.format_exc()
        session.state.crash_traceback = tb
        message = str(exc) or type(exc).__name__
        kind = type(exc).__name__
        logger.exception(
            "Optimization halted at round %d — the optimizer prompt could not be composed. "
            "Fix the composition and resume.",
            round_num,
        )
        return StopReason.RENDER_ERROR, emit_error_record(
            kind=kind, message=message, stop_reason="RENDER_ERROR", traceback=tb
        )
    except TimeoutError:
        # Optimizer LLM blew deadline twice (provider stalled mid-stream); plain ``resume`` re-fires.
        logger.warning(
            "Optimization halted at round %d — an optimizer LLM call exceeded "
            "its deadline twice. Resume to retry.",
            round_num,
        )
        return StopReason.OPTIMIZER_TIMEOUT, None
    except Exception as exc:
        # Escalation flows via return value, not exception; stash traceback for ``_finalize_run`` (sys.exc_info dead by then).
        tb = traceback.format_exc()
        session.state.crash_traceback = tb
        message = str(exc) or type(exc).__name__
        kind = type(exc).__name__
        logger.exception("Optimization crashed at round %d.", round_num)
        return StopReason.CRASHED, emit_error_record(
            kind=kind, message=message, stop_reason="CRASHED", traceback=tb
        )


__all__ = ["HARD_CAP", "run_round_loop"]
