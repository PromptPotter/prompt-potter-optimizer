from __future__ import annotations

import operator
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.intelligence.hard_sample_sorter import rank_hard_samples
from promptpotter.application.optimizers.nodes import ReviewReading
from promptpotter.application.pipeline_resolve import RunResolution
from promptpotter.application.scoring.closed_rounds import closed_rounds, cycle_bank
from promptpotter.application.scoring.measurement_log import cycle_hard_samples
from promptpotter.application.served_dashboard import lookahead_axes
from promptpotter.application.views.render.ansi import (
    render_bench_scored,
    render_pass_graded,
    render_population,
    render_round_start,
    render_round_verdict,
    render_run_spend,
    render_spend_breakdown,
    to_text,
)
from promptpotter.application.views.render.candidate import (
    fmt_individual_header,
    individual_summary,
)
from promptpotter.application.views.render.heatmap import render_hard_sample_heatmap
from promptpotter.application.views.render.phase import (
    acts_line,
    fmt_elapsed,
    render_advance_status,
    render_cycle_final,
    render_holds,
    render_progress_table,
    render_round_block,
    render_round_stats,
    round_verdict_basis,
    stop_lines,
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
    _node_bottom,
    _node_line,
    _node_top,
    _round_rule,
    display_tags,
)
from promptpotter.application.views.render.review import (
    render_cycle_review,
    render_round_review,
    schema_repairs,
)
from promptpotter.application.views.render.sample import fmt_query_result
from promptpotter.domain.arm_reading import ScoreboardRankKey, scoreboard_rank_key
from promptpotter.domain.arms import (
    Concluded,
    Cut,
    Measured,
    Measuring,
    Proposed,
    arm_elimination,
    arm_name,
)
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.connector import MeasuredUnit
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.dashboard_rows import served_rounds
from promptpotter.domain.narration import NarratedRecord, Narration
from promptpotter.domain.paired_reading import level_text, lift_text
from promptpotter.domain.pass_kinds import PASSES
from promptpotter.domain.phase_acts import (
    CellFormula,
    InitClosed,
    InitOpened,
    ProposeClosed,
    ProposeOpened,
)
from promptpotter.domain.phases import (
    RUN_PHASE_INFO,
    CampaignPhase,
    StopOutcome,
    stop_reason_outcome,
)
from promptpotter.domain.records.cell_beat import CellBeatRecord
from promptpotter.domain.records.fold_basis import FoldBasisRecord
from promptpotter.domain.records.held_out import BenchScoredRecord, PassGradedRecord
from promptpotter.domain.records.run_records import (
    ArmRecord,
    ArmsGate,
    CycleFinalRecord,
    ElectionRecord,
    Ended,
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
    Paused,
    PhaseRecord,
    ProviderEasedRecord,
    ProviderHoldRecord,
    RaceCatchUpRecord,
    RaceStandingRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
    RoundProposedRecord,
    RoundStatement,
    RoundWarningRecord,
    RunPhaseRecord,
    RunWiringRecord,
    SampleScoredRecord,
    SampleStartedRecord,
)
from promptpotter.domain.results import RoundResult
from promptpotter.domain.round_audit import RoundAudit
from promptpotter.domain.said import GLYPH
from promptpotter.domain.sample import sample_tag
from promptpotter.infrastructure.projections.audit_trail import round_audits
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_spend,
    scan_resolved_run,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.dataset_access import ResolvedDataset
from promptpotter.infrastructure.store.io import append_line
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.composite import render_composite_fitness_block
from promptpotter.shared.errors import NotFoundError
from promptpotter.shared.measurement_context import MeasurementRole

# A host's readout-line sink. ``None`` is silent; the readout is on disk either way.
StatusFn = Callable[[str], None]

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class ReadoutMissedInitError(RuntimeError):
    """A propose act read before the InitClosed that seats the rounds it stands on."""


class ReadoutProjection(Projection):
    derives = "readout.log"

    def __init__(
        self,
        stores: Stores,
        res: RunResolution,
        dataset: ResolvedDataset | None,
        *,
        measured_unit: MeasuredUnit,
        sink: StatusFn | None,
    ) -> None:
        self._stores, self._res, self._dataset = stores, res, dataset
        self._round_num = 0
        self._sink = sink
        self._schema = res.chain
        self._display_tags = display_tags(self._schema)
        # The round's opening, whose proposer and parent its population's table reads.
        self._opened: ProposeOpened | None = None
        self.measured_unit = measured_unit
        # Each round's LAST close, so a round re-closed on a warmed ruler shows the θ that stands.
        self._closed: dict[int, RoundStatement] = {}
        # The fold of `closed_rounds` over the statements it was read at.
        self._folded: tuple[tuple[RoundStatement, ...], list[RoundResult]] | None = None
        self.sample_counter = 0
        # Folded off the launch's INIT, and the wiring's display metric.
        self._formula: CellFormula | None = None
        # The look-ahead last read: cells per candidate, candidates at once.
        self._sample_lookahead: tuple[int, int | Literal["all"]] | None = None
        self._round_best_key: ScoreboardRankKey | None = None
        self._round_best_fitness: float | None = None
        self._round_best_label: str | None = None
        self._round_started_at: float | None = None
        self._standing_printed_for: str = ""
        self._block_racing: dict[int, int] = {}
        self._block_decided: dict[int, list[tuple[str, bool]]] = {}
        self._blocks_of = 0
        self._block_size = 1
        self._headed: set[int] = set()
        self._readout: Path | None = None
        self._campaigns = stores.campaigns
        self._hop: CycleHop | None = None
        # The election and a race speak in keys; the narration names each arm as announced.
        self._narration = Narration()
        self._order_shown: list[int] = []

    def open_readout(self, hop: CycleHop) -> None:
        """A fork rebinds here; the file it leaves ends on the line naming the next one."""
        path = CycleLayout(self._campaigns.cycle_dir(hop).absolute()).readout
        line = f"Readout: {path} · {time.strftime('%Y-%m-%d %H:%M:%S')}"
        self._write(line)
        self._readout = path
        self._hop = hop
        self._mirror(line)

    def _write(self, line: str) -> None:
        if self._sink is not None:
            self._sink(line)
        self._mirror(line)

    def _mirror(self, line: str) -> None:
        if self._readout is None:
            return
        try:
            append_line(self._readout, _ANSI_RE.sub("", line).encode("utf-8"))
        except (OSError, NotFoundError):
            # Said once, as the ledger's `derived_file_stale`: a readout that just ends reads as a
            # run that stopped.
            self._readout = None
            raise

    def _handle_phase(self, record: PhaseRecord) -> None:
        self._narration.read(self.at_offset, record)
        if record.phase == CampaignPhase.ORIGIN and record.step == "enter":
            self._write("\n" + _round_rule("ROUND 0 — ORIGIN", "C0 · campaign root"))
        act = record.act
        if isinstance(act, InitOpened | InitClosed):
            self._formula = act.formula
        match act:
            case None:
                rendered = ""
            case ProposeOpened():
                parent = self._closed_round(act.parent_round)
                rendered = render_round_start(act, record.round, parent, self._schema)
                self._opened = act
                self._round_best_key = None
                self._round_best_fitness = None
                self._round_best_label = None
                self._round_started_at = time.monotonic()
            case ProposeClosed():
                assert record.round is not None, "a population is its round's"
                assert self._opened is not None, "a population closes the round its opening opened"
                rendered = render_population(
                    act,
                    record.round,
                    proposer=self._opened.node,
                    origin=self._closed_round(0),
                    parent=self._closed_round(self._opened.parent_round),
                    arms=[
                        self._narration.state(record.round, k)
                        for k in self._narration.announced(record.round)
                    ],
                    schema=self._schema,
                )
            case _:
                rendered = to_text(record.phase, act)
        if rendered:
            self._write(rendered)
        if record.round is not None:
            self._round_num = record.round
        # A `--from` retracted its rounds before INIT exits, so what stands is exactly the prior line.
        if isinstance(act, InitClosed) and self._hop is not None:
            chain = self._campaigns.chain(self._hop)
            standing = scan_standing_rounds(chain).rounds
            self._closed = {n: held.close for n, held in standing.items()}
            if (resolved := scan_resolved_run(chain)).moved_panel is not None:
                self._write(
                    f"  {YELLOW}{GLYPH['warn']} a DIFFERENT panel than cycle "
                    f"{resolved.experiment_cycle_id} measured: its roster stands, and this run's "
                    f"rows compare to none before · pin the backend's roster or start anew{RESET}"
                )

    def _closed_round(self, round_num: int) -> RoundStatement:
        """A propose act reads the rounds standing at INIT's exit, and each close since."""
        if (closed := self._closed.get(round_num)) is None:
            raise ReadoutMissedInitError(
                f"the readout holds no close of round {round_num}: it missed the InitClosed that "
                f"seats the standing rounds (it holds {sorted(self._closed)})"
            )
        return closed

    def _handle_run_phase(self, record: RunPhaseRecord) -> None:
        if record.spend is not None:
            self._write(render_run_spend(record.spend))
            self._stop_digest()
        state = record.state
        if not isinstance(state, Paused | Ended):
            # The arms gate is its narration row, then the act that answers it.
            self._say(record)
            if isinstance(state, ArmsGate) and (held := RUN_PHASE_INFO[state.run_phase].refusal):
                for line in acts_line(held.next_acts):
                    self._write(f"    {line}")
            return
        # Every entry point's readout ends on why, so no completion box repeats it; this block is
        # the stop's narration row.
        self._narration.read(self.at_offset, record)
        paused = state if isinstance(state, Paused) else None
        head, *rest = stop_lines(
            state.stop_reason,
            detail=state.detail,
            cause=None if paused is None else paused.cause,
        )
        ink = DIM if stop_reason_outcome(state.stop_reason) is StopOutcome.SUCCESS else YELLOW
        self._write(f"\n  {ink}■ {head}{RESET}")
        for line in rest:
            self._write(f"    {line}")

    def _stop_digest(self) -> None:
        """What a stop reads off the cycle's ledger: its spend per bucket, each hold on the last
        scale, the optimizer's review and the hard samples."""
        hop, config = self._hop, self._res.config
        assert hop is not None, "a run stops on the readout it opened"
        for line in render_spend_breakdown(scan_ledger_spend(self._campaigns.chain(hop)).spend):
            self._write(f"{DIM}{line}{RESET}")
        # A run stopped before its origin closed has no round and may have no scorer either.
        if not self._closed:
            return
        closed = [self._closed[n].summary() for n in sorted(self._closed)]
        last_scale = self._closed[max(self._closed)].level_series
        for line in render_holds(served_rounds(closed, {}, last_scale)):
            self._write(line)
        _, audits, review = self._reviewed()
        for line in render_cycle_review(review, audits, config.optimization.optimizer):
            self._write(line)
        hard = cycle_hard_samples(self._stores, hop, self._rounds(), config)
        measured = hard.ranked_keys
        order, ranked = rank_hard_samples(
            hard,
            {key: hard.positions[key] for key in measured},
            measured=set(measured),
            order=config.hard_sample_order,
        )
        queries = {sample.key: sample.query for sample in cycle_bank(self._stores, hop).samples}
        for line in render_hard_sample_heatmap(hard, queries, order, ranked):
            self._write(line)

    def _rounds(self) -> list[RoundResult]:
        """The closed rounds, folded once per standing set: the review at a close and the stop's
        review and hard samples read one fold, and a re-close folds again."""
        stood = tuple(self._closed[n] for n in sorted(self._closed))
        if self._folded is None or not (
            len(self._folded[0]) == len(stood) and all(map(operator.is_, self._folded[0], stood))
        ):
            hop = self._hop
            assert hop is not None, "a round closes on the readout it opened"
            self._folded = (stood, closed_rounds(self._stores, hop, self._res.scorer, moment=None))
        return self._folded[1]

    def _reviewed(self) -> tuple[list[RoundResult], list[RoundAudit | None], ReviewReading | None]:
        """The optimizer's review of every round the cycle closed, read off its ledger."""
        hop = self._hop
        assert hop is not None, "a round closes on the readout it opened"
        rounds = self._rounds()
        audits = round_audits(self._campaigns.cycle_dir(hop), [r.round for r in rounds])
        framing = campaign_framing(self._stores, self._res.config, self._dataset)
        asked = (framing.pipeline_purpose, framing.optimization_goals, framing.key_challenges)
        review = self._res.optimizer.review(
            rounds, audits, context_object=[c for c in asked if c.strip()]
        )
        return rounds, audits, review

    def _handle_pass_graded(self, record: PassGradedRecord) -> None:
        self._write(render_pass_graded(record))

    def _handle_cycle_final(self, record: CycleFinalRecord) -> None:
        for line in render_cycle_final(record.final):
            self._write(line)

    def _handle_bench_scored(self, record: BenchScoredRecord) -> None:
        self._write(render_bench_scored(record))

    def _handle_run_wiring(self, record: RunWiringRecord) -> None:
        if (drift := record.defaults_drift) is not None:
            self._write(
                f"  {YELLOW}⚠ what this campaign's declaration is a delta over moved since it was "
                f"declared ({', '.join(drift.moved())}): this launch runs today's values{RESET}"
            )
        if record.backend_revision is not None and (said := record.backend_revision.drift()):
            self._write(f"  {YELLOW}⚠ {said}: this launch measures on it as it is{RESET}")

    def _handle_round_proposed(self, record: RoundProposedRecord) -> None:
        self._say(record)

    def _handle_round_closed(self, record: RoundClosedRecord) -> None:
        """A round already closed here is stated again: a restatement announces nothing."""
        restated = record.round in self._closed
        self._closed[record.round] = record
        if not restated:
            self._narration.read(self.at_offset, record)
            self.on_round_complete(record)
            return
        election = record.election
        if election is not None and election.selected and (stated := election.reelection):
            picked = arm_name(election.selected, record.arms[election.selected], record.round)
            self._write(
                f"  {DIM}· r{record.round} re-elected {picked.label} by {stated.by.display}: "
                f"{stated.reason}{RESET}"
            )

    def _say(self, record: NarratedRecord) -> None:
        """The narration's row as one line: its tone's glyph, its label, then its detail."""
        if (row := self._narration.read(self.at_offset, record)) is not None:
            said = row.said
            ink = YELLOW if said.tone in ("attention", "warn", "danger") else DIM
            tail = "" if said.detail is None else f" · {said.detail}"
            self._write(f"  {ink}{GLYPH[said.tone]} {said.label}{tail}{RESET}")

    def _handle_llm_call_start(self, record: LLMCallStartRecord) -> None:
        self._say(record)

    def _handle_llm_call(self, record: LLMCallRecord) -> None:
        self._say(record)

    def _handle_llm_call_progress(self, record: LLMCallProgressRecord) -> None:
        self._say(record)

    def _handle_cell_beat(self, record: CellBeatRecord) -> None:
        self._say(record)

    def _handle_round_warning(self, record: RoundWarningRecord) -> None:
        self._say(record)

    def _handle_provider_hold(self, record: ProviderHoldRecord) -> None:
        self._say(record)

    def _handle_provider_eased(self, record: ProviderEasedRecord) -> None:
        self._say(record)

    def _handle_election(self, record: ElectionRecord) -> None:
        """One line and no numbers: the board, reason and overlap series first all exist at round close."""
        if not record.verdict_reason:
            return
        key = record.selected
        self._write(
            f"  {DIM}· held the best-so-far{RESET}"
            if key is None
            else f"  {GREEN}✓ selected {self._narration.label(key) or key[:6]}{RESET}"
        )

    def _handle_sample_started(self, record: SampleStartedRecord) -> None:
        window, qt = (record.sample_lookahead, record.sample_abreast), record.sample_total
        if window == self._sample_lookahead:
            return
        opening = self._sample_lookahead is None
        self._sample_lookahead = window
        armed = window != (1, 1)
        if armed and PASSES[record.role].max_speed:
            tail = f" (a {record.role.value} pass runs at Max speed; no press reaches it)"
        elif opening and not armed:
            tail = (
                f" — {qt} samples run one at a time; `set-sample-lookahead N|max --abreast N|all` "
                "overlaps them"
                if qt > 1
                else ""
            )
        elif opening:
            tail = f" — of {qt} samples (armed)" if qt else " (armed)"
        elif armed:
            tail = " (armed — expires when this round finishes scoring)"
        else:
            tail = " (back to sequential)"
        self._write(f"  {DIM}⇉ sample look-ahead {lookahead_axes(*window)}{tail}{RESET}")

    def _handle_round_entered(self, record: RoundEnteredRecord) -> None:
        self._narration.read(self.at_offset, record)

    def _handle_fold_basis(self, record: FoldBasisRecord) -> None:
        self._narration.read(self.at_offset, record)

    def _handle_sample_scored(self, record: SampleScoredRecord) -> None:
        self._narration.read(self.at_offset, record)
        self.sample_counter += 1
        facts, grade = record.result.facts, record.result.grade
        prefix = f"  [{self.sample_counter:>3d}] "
        tags = self._display_tags
        self._write(
            fmt_query_result(record.position, facts, grade, prefix=prefix, display_tags=tags)
        )

    def _handle_arm(self, record: ArmRecord) -> None:
        state = record.state
        # A proposal's line is its variation chain; one with none says nothing yet.
        if isinstance(state, Proposed) and state.individual.lineage.variations:
            self._say(record)
            return
        self._narration.read(self.at_offset, record)
        # A reference re-read is no arm the round proposed: its numbers print on the verdict line.
        if state.role is MeasurementRole.PARENT:
            return
        match state:
            case Measuring():
                self.on_sample_order(state.sample_order, state.n_priors)
                self.on_arm_measuring(state)
            case Cut() | Measured():
                self.on_arm_concluded(state, self._narration.fold(record.round, record.arm))

    def _handle_race_standing(self, record: RaceStandingRecord) -> None:
        current, breakdown = record.current, record.paired_breakdown
        if self._block_racing or not current or current == self._standing_printed_for:
            return
        self._standing_printed_for = current
        hardest_tag, hardest_p = "*self*", record.p_best
        if breakdown:
            hardest_id, hardest = min(breakdown.items(), key=lambda kv: kv[1])
            hardest_tag, hardest_p = hardest_id[:6], hardest
        n = len(breakdown)
        self._write(
            f"  {DIM}{record.member}:{RESET} P(best)={record.p_best:.1%} @ q{record.n_samples}  "
            f"vs hardest={hardest_tag} (P(c>p)={hardest_p:.1%})  "
            f"(of {n} prior{'' if n == 1 else 's'})"
        )

    def _handle_race_catch_up(self, record: RaceCatchUpRecord) -> None:
        if record.prior_keys:
            tags = ", ".join(self._narration.label(k) or k[:6] for k in record.prior_keys)
            self._write(
                f"  {DIM}↻ {record.member} catch-up {sample_tag(record.position)}:{RESET} {tags}"
            )

    def on_sample_order(self, sample_order: list[int], n_priors: int) -> None:
        """Once per order: every turn of a round's walk announces the one it shares."""
        if not sample_order or self._block_racing or sample_order == self._order_shown:
            return
        self._order_shown = sample_order
        prior_s = "" if n_priors == 1 else "s"
        head = ", ".join(sample_tag(sid) for sid in sample_order[:3])
        self._write(
            f"  {DIM}shared order:{RESET} {head}, …  "
            f"({len(sample_order)} samples, win-opportunities first; "
            f"{n_priors} candidate prior{prior_s})"
        )

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

    def on_arm_measuring(self, arm: Measuring) -> None:
        assert arm.position is not None
        label, idx, total, block = arm.label, arm.position.idx, arm.position.total, arm.block
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
        self._write(
            fmt_individual_header(label, total, arm.changes_description, arm.overlay or None)
        )

    def on_arm_concluded(self, scores: Concluded, fold: ScoreSummary | None) -> None:
        w = 66
        assert scores.position is not None
        label, total = scores.label, scores.position.total
        cut = isinstance(scores, Cut)
        if self._block_racing and fold is not None and (cut or arm_elimination(scores)):
            block = -(-fold.walked // self._block_size)
            self._block_decided.setdefault(block, []).append((label, cut))
        # No reading yet: the election that pairs it against its reference has not run.
        summary = individual_summary(scores, fold, unit=self.measured_unit)

        self._write(f"  {_box_top(f'{label}/{total}', summary.tag, width=w)}")
        if summary.body_line:
            self._write(f"  {_box_line(summary.body_line, width=w)}")
        for line in summary.detail_lines[:-1]:
            self._write(f"  {_box_line(line, width=w)}")
        if summary.detail_lines:
            self._write(f"  {_box_bottom_info(summary.detail_lines[-1], width=w)}")
        else:
            self._write(f"  {_box_bottom(width=w)}")

        if fold is not None and fold.fitness is not None:
            self._write(self._fmt_round_leader(label, fold.fitness, fold))

    def _fmt_round_leader(self, label: str, fitness: float, fold: ScoreSummary) -> str:
        key, unit = scoreboard_rank_key(fold.composite_fitness, fitness), fold.fitness_unit
        new_round_max = self._round_best_key is None or key > self._round_best_key
        if new_round_max:
            self._round_best_key = key
            self._round_best_fitness = fitness
            self._round_best_label = label
            return f"  {GREEN}★ leader: {label} {level_text(fitness, unit)}{RESET}"
        gap = fitness - (self._round_best_fitness or fitness)
        prior = self._round_best_label or "leader"
        lead = f"{level_text(fitness, unit)}  ({lift_text(gap, unit)} from {prior})"
        return f"  {DIM}→ {label} {lead}{RESET}"

    def on_round_complete(self, round_result: RoundClosedRecord) -> None:
        self.sample_counter = 0
        series = round_result.level_series

        # Never `self._round_num`: the block race advances it before this round's summary prints.
        rn = round_result.round
        elapsed_label = ""
        if self._round_started_at is not None:
            elapsed = time.monotonic() - self._round_started_at
            elapsed_label = f" — {fmt_elapsed(elapsed)}"
        self._round_started_at = None
        # Round 0 is the origin's read, no election's verdict.
        verdict = rn > 0
        if verdict:
            self._write(
                render_round_verdict(
                    round_result,
                    round_verdict_basis(round_result),
                    series.levels[rn],
                    cell_formula=self._formula,
                )
            )
        self._write("")
        self._write(_node_top(f"ROUND {rn} SUMMARY{elapsed_label}"))
        standing = round_result.run_standing
        if (climb := standing.climb) is not None:
            self._write(_node_line(climb.line))
            self._write(_node_line(f"  {standing.score_line}"))
        table = render_progress_table([self._closed[n] for n in sorted(self._closed)], series)
        for line in table.split("\n"):
            self._write(line)
        for line in self._render_block_lines():
            self._write(_node_line(line))
        self._block_racing, self._block_decided, self._headed = {}, {}, set()
        formula_short = None if self._formula is None else self._formula.short
        formula_full = None if self._formula is None else self._formula.formula
        fold, election = round_result.fold, round_result.election
        composite = None if fold is None else fold.composite_fitness
        if (
            not verdict
            and fold is not None
            and election is not None
            and composite is not None
            and (formula_short or formula_full)
        ):
            # No round-0 fallback reference: a cross-subset one reads draw difficulty as candidate lift.
            for line in render_composite_fitness_block(
                composite,
                dict(fold.evaluators),
                formula_short or formula_full,
                reference=None
                if election.selected is None
                or (pair := round_result.vs_reference(election.selected)) is None
                else pair.reference_level("objective"),
                use_short_names=bool(formula_short),
            ):
                self._write(_node_line(line))
        if stats := render_round_stats(round_result, self.measured_unit):
            for line in stats.split("\n"):
                if line:
                    self._write(line)
        closed = [self._closed[n].summary() for n in sorted(self._closed)]
        served = next(r for r in served_rounds(closed, {}, series) if r.round == rn)
        for line in render_round_block(served):
            self._write(line)
        rounds, audits, review = self._reviewed()
        if (at := next((i for i, r in enumerate(rounds) if r.round == rn), None)) is not None:
            for line in render_round_review(
                [] if review is None else review.checks[at], schema_repairs(audits[at])
            ):
                self._write(line)
        self._write(
            render_advance_status(
                round_result.origin_panel.reading, standing.rounds_without_advance
            )
        )
        self._write(_node_bottom())
