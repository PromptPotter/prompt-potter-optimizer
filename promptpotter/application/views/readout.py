from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from promptpotter.application.initialization.session import Session
from promptpotter.application.scoring.formula import split_scoring_block
from promptpotter.application.views.render.ansi import (
    render_round_verdict,
    render_run_spend,
    to_text,
)
from promptpotter.application.views.render.candidate import (
    fmt_individual_header,
    individual_summary,
)
from promptpotter.application.views.render.phase import (
    fmt_elapsed,
    render_patience_status,
    render_progress_table,
    render_round_stats,
    round_verdict_basis,
)
from promptpotter.application.views.render.primitives import (
    DIM,
    GREEN,
    RESET,
    YELLOW,
    _box_bottom,
    _box_bottom_info,
    _box_line,
    _box_top,
    _fmt_delta,
    _node_bottom,
    _node_line,
    _node_top,
    _round_rule,
    display_tags,
)
from promptpotter.application.views.render.sample import fmt_query_result
from promptpotter.domain.connector import MeasuredUnit
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.phase_views import (
    InitExitView,
    RoundCompleteView,
    RoundStartView,
    ViewAnchors,
)
from promptpotter.domain.phases import CampaignPhase
from promptpotter.domain.results import (
    ArmOutcome,
    ScoreboardRankKey,
    ScoredCandidate,
    candidate_label,
    scoreboard_rank_key,
)
from promptpotter.domain.run_records import (
    CandidateMintedRecord,
    CandidateScoredRecord,
    CandidateStartedRecord,
    ElectionRecord,
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
    PhaseRecord,
    RaceCatchUpRecord,
    RaceStandingRecord,
    RoundClosedRecord,
    RoundStandingRecord,
    RoundWarningRecord,
    RunPhaseRecord,
    SampleOrderRecord,
    SampleScoredRecord,
    SampleStartedRecord,
    scored_cell,
)
from promptpotter.domain.spend import TokenAccount, prefix_reading
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_standing_rounds
from promptpotter.infrastructure.store.io import append_line
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.judges.registry import judge_instrument
from promptpotter.shared.composite import render_composite_fitness_block

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import Grade, MeasuredCell


logger = logging.getLogger(__name__)

# A host's readout-line sink. ``None`` is silent; the readout is on disk either way.
StatusFn = Callable[[str], None]

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class ReadoutProjection(Projection):
    def __init__(
        self,
        *,
        sink: StatusFn | None,
        patience: int | None,
        pipeline_schema: PipelineSchema | None,
        scoring_formula: str | None = None,
        measured_unit: MeasuredUnit = "sample",
    ) -> None:
        self._round_num = 0
        self._sink = sink
        self.patience = patience
        self.pipeline_schema = pipeline_schema
        self._display_tags = display_tags(pipeline_schema)
        self.scoring_formula = scoring_formula
        self.measured_unit = measured_unit
        # Each round's LAST close, so a round re-closed on a warmed ruler shows the θ that stands.
        self._closed: dict[int, RoundClosedRecord] = {}
        self.sample_counter = 0
        self._anchors = ViewAnchors()
        self._sample_lookahead_depth: int | None = None
        self._round_best_key: ScoreboardRankKey | None = None
        self._round_best_acc: float | None = None
        self._round_best_label: str | None = None
        self._round_started_at: float | None = None
        self._standing_printed_for: str = ""
        self._block_racing: dict[int, int] = {}
        self._block_decided: dict[int, list[tuple[str, bool]]] = {}
        self._blocks_of = 0
        self._block_size = 1
        self._headed: set[int] = set()
        self._pending_calls: dict[str, int] = {}
        self._readout: Path | None = None
        self._cycle_dir: Path | None = None
        self._verdict: RoundCompleteView | None = None

    @classmethod
    def for_campaign(
        cls,
        session: Session,
        campaign_config: CampaignConfig,
        *,
        sink: StatusFn | None,
    ) -> ReadoutProjection:
        return cls(
            sink=sink,
            patience=campaign_config.optimization.convergence_patience,
            pipeline_schema=session.pipeline_schema,
            scoring_formula=split_scoring_block(
                campaign_config.scoring, judge_instrument=judge_instrument(campaign_config.judges)
            ).per_sample,
            measured_unit=session.backend_client.measured_unit,
        )

    def open_readout(self, cycle_dir: Path) -> None:
        """A fork rebinds here; the file it leaves ends on the line naming the next one."""
        path = CycleLayout(cycle_dir.absolute()).readout
        line = f"Readout: {path} · {time.strftime('%Y-%m-%d %H:%M:%S')}"
        self._write(line)
        self._readout = path
        self._cycle_dir = cycle_dir
        self._mirror(line)

    def _write(self, line: str) -> None:
        if self._sink is not None:
            self._sink(line)
        self._mirror(line)

    def _mirror(self, line: str) -> None:
        if self._readout is None:
            return
        try:
            append_line(self._readout, _ANSI_RE.sub("", line))
        except OSError as exc:
            # Said once: a readout that just ends reads as a run that stopped.
            logger.warning("readout.log stopped at %s: %s", self._readout, exc)
            self._readout = None

    def _handle_phase(self, record: PhaseRecord) -> None:
        if record.phase == CampaignPhase.ORIGIN and record.event == "enter":
            self._write("\n" + _round_rule("ROUND 0 — ORIGIN", "C0 · campaign root"))
        view = record.view
        if isinstance(view, RoundCompleteView):
            # Held, not printed: the panel gate can still unwind this round.
            self._verdict = view
        elif view is not None and (rendered := to_text(view)):
            self._write(rendered)
        if record.round is not None:
            self._round_num = record.round
        if isinstance(view, RoundStartView):
            self._round_best_key = None
            self._round_best_acc = None
            self._round_best_label = None
            self._round_started_at = time.monotonic()
        # `> 1`, not `> 0`: `resumed_from_round` counts the NEXT round, so a fresh mint is 1.
        if (
            isinstance(view, InitExitView)
            and view.resumed_from_round > 1
            and self._cycle_dir is not None
        ):
            standing = scan_standing_rounds(ledger_chain(CycleDir(self._cycle_dir))).rounds
            self._closed = {
                n: held.close for n, held in standing.items() if n < view.resumed_from_round
            }

    def _handle_run_phase(self, record: RunPhaseRecord) -> None:
        if record.spend is not None:
            self._write(render_run_spend(record.spend))

    def _handle_round_standing(self, record: RoundStandingRecord) -> None:
        self._anchors = record.anchors
        self.on_round_complete(
            self._closed[record.round], record.run_standing.rounds_without_advance
        )

    def _handle_round_closed(self, record: RoundClosedRecord) -> None:
        self._closed[record.round] = record

    def _handle_llm_call_start(self, record: LLMCallStartRecord) -> None:
        self._pending_calls[record.call_id] = record.started_at_ms
        model = record.model or "(default)"
        round_tag = f"r{record.round}" if record.round is not None else ""
        node_label = f"{record.node}_{round_tag}" if round_tag else record.node
        # The alarm is a REFUSED panel, never a big prompt: a mandatory floor is admitted whatever it costs.
        refused = record.refused_panels
        marker = "⚠ " if refused else "↻ "
        bits = [f"{marker}optimizer call: {node_label} · {model}"]
        if record.prompt_chars > 0:
            bits.append(f"{record.prompt_chars:,}c prompt")
        if refused:
            bits.append(f"NO ROOM: {', '.join(refused[:3])}")
        color = YELLOW if refused else DIM
        self._write(f"  {color}{' · '.join(bits)}{RESET}")

    def _handle_round_warning(self, record: RoundWarningRecord) -> None:
        round_tag = f"r{record.round}" if record.round is not None else ""
        glyph = "✗" if record.severity == "error" else "⚠"
        prefix = f"{glyph} {round_tag}".rstrip()
        self._write(f"  {YELLOW}{prefix} {record.message}{RESET}")

    def _handle_llm_call_progress(self, record: LLMCallProgressRecord) -> None:
        """``detail`` prints VERBATIM: a bare tick proves only liveness, and a healthy inner campaign reads as frozen."""
        round_tag = f"r{record.round}" if record.round is not None else ""
        node_label = f"{record.node}_{round_tag}" if round_tag else record.node
        line = f"  · {node_label} still waiting · {record.elapsed_s:.0f}s"
        if record.detail:
            line += f" · {record.detail}"
        self._write(f"  {DIM}{line}{RESET}")

    def _handle_llm_call(self, record: LLMCallRecord) -> None:
        payload = record.payload
        cached = bool(payload.get("cached"))
        started = self._pending_calls.pop(record.call_id, None) if record.call_id else None
        duration_s = payload.get("duration_s")
        if duration_s is None and started is not None:
            duration_s = max(0.0, (time.time() * 1000 - started) / 1000.0)
        usage = TokenAccount.from_payload(payload.get("usage"))
        round_tag = f"r{record.round}" if record.round is not None else ""
        node_label = f"{record.node}_{round_tag}" if round_tag else record.node
        bits: list[str] = [node_label]
        if isinstance(duration_s, (int, float)):
            bits.append(f"{duration_s:.1f}s")
        if usage.total > 0:
            bits.append(f"{usage.total} tok")
        if usage.reasoning > 0 and usage.output > 0:
            bits.append(f"{usage.reasoning / usage.output:.0%} reasoning")
        prefix = prefix_reading(usage.cache_share(replayed=cached), replayed=cached)
        if prefix.state == "unreported":
            bits.append("prefix not reported")
        elif prefix.share is not None:
            bits.append(f"{prefix.share:.0%} prefix cached")
        # "replayed", not "cached": OUR archive served it; `cached` names the provider-side discount above.
        if cached:
            bits.append("replayed")
        flags: list[str] = []
        if retries := len(payload.get("schema_repair_errors") or ()):
            flags.append(f"re-asked {retries}x")
        if payload.get("finish_reason") == "length":
            flags.append("TRUNCATED at max_tokens")
        lead = f"{YELLOW}⚠" if flags else f"{DIM}✓"
        self._write(f"  {lead} {' · '.join([*bits, *flags])}{RESET}")

    def _handle_election(self, record: ElectionRecord) -> None:
        """One line and no numbers: the board, reason and overlap series first all exist at round close."""
        if not record.verdict_reason:
            return
        self._write(
            f"  {GREEN}✓ selected {', '.join(record.selected_labels)}{RESET}"
            if record.selected_labels
            else f"  {DIM}· held the best-so-far{RESET}"
        )

    def _handle_sample_started(self, record: SampleStartedRecord) -> None:
        depth, qt = record.sample_lookahead, record.sample_total
        if depth == self._sample_lookahead_depth:
            return
        opening = self._sample_lookahead_depth is None
        self._sample_lookahead_depth = depth
        if opening and depth <= 1:
            tail = (
                f" — {qt} samples run one at a time; arm it from the browser to overlap them"
                if qt > 1
                else ""
            )
        elif opening:
            tail = f" — up to {depth} of {qt} in flight (armed)" if qt else " (armed)"
        elif depth > 1:
            tail = " (armed — expires when this round finishes scoring)"
        else:
            tail = " (back to sequential)"
        self._write(f"  {DIM}⇉ sample look-ahead depth {depth}{tail}{RESET}")

    def _handle_sample_scored(self, record: SampleScoredRecord) -> None:
        self.on_sample_scored(*scored_cell(record.result))

    def _handle_candidate_minted(self, record: CandidateMintedRecord) -> None:
        if chain := " → ".join(
            f"{v.node} ({v.mode}): {', '.join(v.loci) or 'nothing'}"
            for v in record.lineage.variations
        ):
            self._write(f"  {DIM}◆ {record.label} ← {chain}{RESET}")

    def _handle_candidate_started(self, record: CandidateStartedRecord) -> None:
        self.on_candidate_started(
            record.candidate_idx,
            record.candidate_total,
            record.changes_description,
            record.pipeline_overlay,
            record.block,
        )

    def _handle_candidate_scored(self, record: CandidateScoredRecord) -> None:
        self._anchors = record.anchors
        self.on_candidate_scored(record.candidate_total, record.scores)

    def _handle_race_standing(self, record: RaceStandingRecord) -> None:
        self.on_race_standing(
            record.member,
            record.current_id,
            record.n_samples,
            record.p_best,
            record.paired_breakdown,
            record.decision_grade,
        )

    def _handle_sample_order(self, record: SampleOrderRecord) -> None:
        self.on_sample_order(record.sample_order, record.n_priors)

    def _handle_race_catch_up(self, record: RaceCatchUpRecord) -> None:
        self.on_race_catch_up(record.member, record.sample_id, record.prior_ids)

    def on_sample_scored(self, facts: MeasuredCell, grade: Grade) -> None:
        self.sample_counter += 1
        prefix = f"  [{self.sample_counter:>3d}] "
        self._write(
            fmt_query_result(
                facts,
                grade,
                prefix=prefix,
                scoring_formula=self.scoring_formula,
                display_tags=self._display_tags,
            )
        )

    def on_race_standing(
        self,
        member: str,
        current_id: str,
        n_samples: int,
        p_best: float,
        paired_breakdown: dict[str, dict[str, float]],
        decision_grade: bool,
    ) -> None:
        if (
            not self._block_racing
            and current_id
            and current_id != self._standing_printed_for
            and decision_grade
        ):
            self._standing_printed_for = current_id
            current_p = p_best
            if paired_breakdown:
                hardest_id, hardest = min(
                    paired_breakdown.items(), key=lambda kv: kv[1].get("p_better", 1.0)
                )
                hardest_tag, hardest_p = hardest_id[:6], hardest.get("p_better", 0.0)
            else:
                hardest_tag, hardest_p = "*self*", current_p
            n_priors = len(paired_breakdown)
            prior_s = "" if n_priors == 1 else "s"
            self._write(
                f"  {DIM}{member}:{RESET} P(best)={current_p:.1%} @ q{n_samples}  "
                f"vs hardest={hardest_tag} (P(c>p)={hardest_p:.1%})  "
                f"(of {n_priors} prior{prior_s})"
            )

    def on_sample_order(self, sample_order: list[int], n_priors: int) -> None:
        if not sample_order or self._block_racing:
            return
        prior_s = "" if n_priors == 1 else "s"
        head = ", ".join(f"#{sid:03d}" for sid in sample_order[:3])
        self._write(
            f"  {DIM}shared order:{RESET} {head}, …  "
            f"({len(sample_order)} samples, win-opportunities first; "
            f"{n_priors} candidate prior{prior_s})"
        )

    def on_race_catch_up(self, member: str, sample_id: int, prior_ids: list[str]) -> None:
        if not prior_ids:
            return
        tags = [cid if cid == "origin" or cid.endswith("_winner") else cid[:6] for cid in prior_ids]
        self._write(f"  {DIM}↻ {member} catch-up #{sample_id}:{RESET} " + ", ".join(tags))

    def _render_block_lines(self) -> list[str]:
        lines = []
        for n, racing in sorted(self._block_racing.items()):
            decided = self._block_decided.get(n, [])
            cut = [label for label, was_cut in decided if was_cut]
            settled = " · settled" if len(cut) < len(decided) else ""
            lines.append(
                f"block {n}/{self._blocks_of}: {racing} raced · "
                f"{'cut ' + ', '.join(cut) if cut else 'none cut'} · "
                f"{racing - len(cut)} survive{settled}"
            )
        return lines

    def on_candidate_started(
        self,
        idx: int,
        total: int,
        changes_description: str,
        pipeline_overlay: dict[str, Any] | None,
        block: dict[str, int] | None = None,
    ) -> None:
        label = candidate_label(self._round_num, idx)
        if block is not None:
            if block["n"] not in self._block_racing:
                self._block_racing[block["n"]] = block["racing"]
                self._blocks_of = block["of"]
                self._block_size = block["size"]
                self._write(
                    f"  {DIM}▦ block {block['n']}/{block['of']} · {block['size']} cells · "
                    f"{block['racing']} arms racing{RESET}"
                )
            if idx in self._headed:
                self._write(f"  {label}/{total}")
                return
            self._headed.add(idx)
        self._write(fmt_individual_header(label, total, changes_description, pipeline_overlay))

    def on_candidate_scored(self, total: int, scores: ScoredCandidate) -> None:
        w = 66
        label, outcome = scores.label, scores.outcome
        if self._block_racing and outcome in (ArmOutcome.ELIMINATED, ArmOutcome.LOCKED_IN):
            block = -(-scores.scored_samples // self._block_size)
            self._block_decided.setdefault(block, []).append(
                (label, outcome == ArmOutcome.ELIMINATED)
            )
        summary = individual_summary(scores, unit=self.measured_unit)

        self._write(f"  {_box_top(f'{label}/{total}', summary.tag, width=w)}")
        if summary.body_line:
            self._write(f"  {_box_line(summary.body_line, width=w)}")
        for line in summary.detail_lines[:-1]:
            self._write(f"  {_box_line(line, width=w)}")
        if summary.detail_lines:
            self._write(f"  {_box_bottom_info(summary.detail_lines[-1], width=w)}")
        else:
            self._write(f"  {_box_bottom(width=w)}")

        if scores.accuracy is not None:
            self._write(
                self._fmt_round_leader(
                    label, scores.accuracy, summary.lift, scores.composite_fitness
                )
            )

    def _fmt_round_leader(
        self, label: str, acc: float, lift: float | None, composite: float | None
    ) -> str:
        """The Δ is the SERVED lift: recomputed here it would crown whichever arm was cut earliest."""
        key = scoreboard_rank_key(composite, acc)
        new_round_max = self._round_best_key is None or key > self._round_best_key
        if new_round_max:
            self._round_best_key = key
            self._round_best_acc = acc
            self._round_best_label = label
            vs = f"  (Δ {_fmt_delta(lift)} vs reference)" if lift is not None else ""
            return f"  {GREEN}★ leader: {label} {acc:.1%}{vs}{RESET}"
        gap = acc - (self._round_best_acc or acc)
        prior = self._round_best_label or "leader"
        return f"  {DIM}→ {label} {acc:.1%}  ({gap:.1%} from {prior}){RESET}"

    def on_round_complete(self, round_result: RoundClosedRecord, stall: int) -> None:
        self.sample_counter = 0

        # Never `self._round_num`: the block race advances it before this round's summary prints.
        rn = round_result.round
        elapsed_label = ""
        if self._round_started_at is not None:
            elapsed = time.monotonic() - self._round_started_at
            elapsed_label = f" — {fmt_elapsed(elapsed)}"
        self._round_started_at = None
        verdict, self._verdict = self._verdict, None
        if verdict is not None:
            self._write(
                render_round_verdict(
                    verdict, round_verdict_basis(round_result), round_result.ability
                )
            )
        self._write("")
        self._write(_node_top(f"ROUND {rn} SUMMARY{elapsed_label}"))
        table = render_progress_table([self._closed[n] for n in sorted(self._closed)])
        for line in table.split("\n"):
            self._write(line)
        for line in self._render_block_lines():
            self._write(_node_line(line))
        self._block_racing, self._block_decided, self._headed = {}, {}, set()
        formula_short = self._anchors.composite_fitness_formula_short
        formula_full = self._anchors.composite_fitness_formula
        composite = round_result.composite_fitness
        if verdict is None and composite is not None and (formula_short or formula_full):
            # No round-0 fallback reference: a cross-subset one reads draw difficulty as candidate lift.
            for line in render_composite_fitness_block(
                composite,
                dict(round_result.evaluators),
                formula_short or formula_full,
                reference=next(
                    (
                        s.vs_reference.reference_level("objective")
                        for s in round_result.selected_scores
                        if s.vs_reference is not None
                    ),
                    None,
                ),
                use_short_names=bool(formula_short),
            ):
                self._write(_node_line(line))
        if stats := render_round_stats(round_result, self.pipeline_schema, self.measured_unit):
            for line in stats.split("\n"):
                if line:
                    self._write(line)
        for fact in (f for f in round_result.optimizer_facts if f.kind == "stat"):
            self._write(_node_line(f"{fact.label}: {fact.text}"))
        for line in render_patience_status(
            round_result.overlap.advance, stall, self.patience
        ).split("\n"):
            self._write(line)
        self._write(_node_bottom())


__all__ = ["ReadoutProjection", "StatusFn"]
