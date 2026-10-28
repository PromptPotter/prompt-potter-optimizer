from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from promptpotter.application.initialization.session import Session
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
from promptpotter.application.views.render.sample import fmt_query_result
from promptpotter.domain.backend import BackpressureReading
from promptpotter.domain.connector import MeasuredUnit
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.phase_views import (
    InitExitView,
    RoundCompleteView,
    RoundStartView,
    ViewAnchors,
)
from promptpotter.domain.phases import CampaignPhase, StopOutcome, stop_reason_outcome
from promptpotter.domain.results import (
    Concluded,
    Cut,
    Measured,
    Measuring,
    Proposed,
    ScoreboardRankKey,
    ScoreSummary,
    arm_elimination,
    scoreboard_rank_key,
)
from promptpotter.domain.run_records import (
    AnnouncedArms,
    ArmRecord,
    ElectionRecord,
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
    PhaseRecord,
    ProviderEasedRecord,
    ProviderHoldRecord,
    RaceCatchUpRecord,
    RaceStandingRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
    RoundProposedRecord,
    RoundRestatedRecord,
    RoundStatement,
    RoundWarningRecord,
    RunPhaseRecord,
    RunWiringRecord,
    SampleScoredRecord,
    SampleStartedRecord,
)
from promptpotter.domain.spend import TokenAccount, prefix_reading
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_standing_rounds
from promptpotter.infrastructure.store.io import append_line
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.composite import render_composite_fitness_block
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.domain.campaign_config import CampaignConfig
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import Grade, MeasuredCell


logger = logging.getLogger(__name__)

# A host's readout-line sink. ``None`` is silent; the readout is on disk either way.
StatusFn = Callable[[str], None]

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _clock(epoch: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(epoch))


class ReadoutProjection(Projection):
    def __init__(
        self,
        *,
        sink: StatusFn | None,
        patience: int | None,
        pipeline_schema: PipelineSchema | None,
        measured_unit: MeasuredUnit = "sample",
    ) -> None:
        self._round_num = 0
        self._sink = sink
        self.patience = patience
        self.pipeline_schema = pipeline_schema
        self._display_tags = display_tags(pipeline_schema)
        self.measured_unit = measured_unit
        # Each round's LAST close, so a round re-closed on a warmed ruler shows the θ that stands.
        self._closed: dict[int, RoundStatement] = {}
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
        self._held: dict[str, BackpressureReading] = {}
        # Each arm's label as its announcement named it: the election and a race speak in keys.
        self._labels: dict[ArmKey, str] = {}
        self._arms = AnnouncedArms()
        self._order_shown: list[int] = []

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
            append_line(self._readout, _ANSI_RE.sub("", line).encode("utf-8"))
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
        # A `--from` retracted its rounds before INIT exits, so what stands is exactly the prior line.
        if isinstance(view, InitExitView) and self._cycle_dir is not None:
            standing = scan_standing_rounds(
                ledger_chain(CycleDir(self._cycle_dir), moment=None)
            ).rounds
            self._closed = {n: held.close for n, held in standing.items()}

    def _handle_run_phase(self, record: RunPhaseRecord) -> None:
        if record.spend is not None:
            self._write(render_run_spend(record.spend))
        if record.stop_reason is not None:
            # Every entry point's readout ends on why, so no completion box repeats it.
            head, *rest = stop_lines(record.stop_reason, detail=record.detail)
            ink = DIM if stop_reason_outcome(record.stop_reason) is StopOutcome.SUCCESS else YELLOW
            self._write(f"\n  {ink}■ {head}{RESET}")
            for line in rest:
                self._write(f"    {line}")
        if record.gate == "arms":
            # ASCII-only: a cp1252 console is one of this line's sinks.
            n = self._round_num
            self._write(f"\n  [ARM GATE] round {n} holds for the operator's arms")
            for idx, draft in enumerate(record.drafts):
                lineage = draft.individual.lineage
                said = lineage.changes_description.encode("ascii", "replace").decode()
                self._write(f"    draft {idx} ({lineage.source or 'unsourced'}): {said or '-'}")
            if not record.drafts:
                self._write("    no proposer ran: every arm this round measures is the operator's")
            self._write("    answer: python -m promptpotter submit-arms --arms <arms.yaml>")

    def _handle_run_wiring(self, record: RunWiringRecord) -> None:
        if (drift := record.defaults_drift) is not None:
            self._write(
                f"  {YELLOW}⚠ what this campaign's declaration is a delta over moved since it was "
                f"declared ({', '.join(drift.moved())}): this launch runs today's values{RESET}"
            )

    def _handle_round_proposed(self, record: RoundProposedRecord) -> None:
        if record.submitted:
            self._write(
                f"  [ARM GATE] round {record.round}: the operator answered with "
                f"{len(record.proposals)} arm(s); each is a round_proposed record on the ledger"
            )

    def _handle_round_restated(self, record: RoundRestatedRecord) -> None:
        self._closed[record.round] = record

    def _handle_round_closed(self, record: RoundClosedRecord) -> None:
        self._closed[record.round] = record
        self._anchors = record.anchors
        self.on_round_complete(record)

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

    def _handle_provider_eased(self, record: ProviderEasedRecord) -> None:
        if self._held.pop(record.sender, None) is not None:
            after = fmt_elapsed(record.held_s)
            self._write(f"  {GREEN}✓ {record.sender} answers again after {after}{RESET}")

    def _handle_provider_hold(self, record: ProviderHoldRecord) -> None:
        sender = record.sender
        held, shown = record.reading, self._held.get(sender)
        if held == shown:
            return
        self._held[sender] = held
        hold = "" if held.resumes_at is None else f" · sends held until {_clock(held.resumes_at)}"
        if held.at_once is not None:
            hold += f", then {held.at_once} at once"
        if shown is not None and shown.since == held.since:
            self._write(
                f"  {YELLOW}⚠ still rate-limited · {sender}, throttle {record.strikes}{hold}{RESET}"
            )
            return
        said = f' · "{held.detail}"' if held.detail else ""
        self._write(
            f"  {YELLOW}⚠ RATE-LIMITED · {sender} ({record.scope}){said}{hold}"
            f" · the run stops if still throttled at {_clock(record.gives_up_at)}{RESET}"
        )

    def _handle_llm_call_progress(self, record: LLMCallProgressRecord) -> None:
        """``detail`` prints VERBATIM: a bare tick proves only liveness, and a healthy inner campaign reads as frozen."""
        round_tag = f"r{record.round}" if record.round is not None else ""
        node_label = f"{record.node}_{round_tag}" if round_tag else record.node
        line = f"  · {node_label} still waiting · {record.elapsed_s:.0f}s"
        if record.detail:
            line += f" · {record.detail}"
        if record.inner is not None:
            line += f" · inner {record.inner.campaign_id}/{record.inner.cycle_id}"
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
        key = record.selected
        self._write(
            f"  {DIM}· held the best-so-far{RESET}"
            if key is None
            else f"  {GREEN}✓ selected {self._labels.get(key, key[:6])}{RESET}"
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

    def _handle_round_entered(self, record: RoundEnteredRecord) -> None:
        self._arms.enter(record)

    def _handle_sample_scored(self, record: SampleScoredRecord) -> None:
        self._arms.take(record)
        self.on_sample_scored(record.position, record.result.facts, record.result.grade)

    def _handle_arm(self, record: ArmRecord) -> None:
        self._arms.announce(record)
        state = record.state
        # A reference re-read is no arm the round proposed: its numbers print on the verdict line.
        if state.role is MeasurementRole.PARENT:
            return
        self._labels[record.arm] = state.label
        match state:
            case Proposed():
                if chain := " → ".join(
                    f"{v.node} ({v.mode}): {', '.join(v.loci) or 'nothing'}"
                    for v in state.individual.lineage.variations
                ):
                    self._write(f"  {DIM}◆ {state.label} ← {chain}{RESET}")
            case Measuring():
                self.on_sample_order(state.sample_order, state.n_priors)
                self.on_arm_measuring(state)
            case Cut() | Measured():
                self._anchors = record.anchors
                self.on_arm_concluded(state, self._arms.fold(record.round, record.arm))

    def _handle_race_standing(self, record: RaceStandingRecord) -> None:
        self.on_race_standing(
            record.member,
            record.current,
            record.n_samples,
            record.p_best,
            record.paired_breakdown,
            record.decision_grade,
        )

    def _handle_race_catch_up(self, record: RaceCatchUpRecord) -> None:
        self.on_race_catch_up(record.member, record.position, record.prior_keys)

    def on_sample_scored(self, position: int, facts: MeasuredCell, grade: Grade) -> None:
        self.sample_counter += 1
        prefix = f"  [{self.sample_counter:>3d}] "
        self._write(
            fmt_query_result(
                position,
                facts,
                grade,
                prefix=prefix,
                display_tags=self._display_tags,
            )
        )

    def on_race_standing(
        self,
        member: str,
        current_id: str,
        n_samples: int,
        p_best: float,
        paired_breakdown: dict[ArmKey, dict[str, float]],
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
        """Once per order: every turn of a round's walk announces the one it shares."""
        if not sample_order or self._block_racing or sample_order == self._order_shown:
            return
        self._order_shown = sample_order
        prior_s = "" if n_priors == 1 else "s"
        head = ", ".join(f"#{sid:03d}" for sid in sample_order[:3])
        self._write(
            f"  {DIM}shared order:{RESET} {head}, …  "
            f"({len(sample_order)} samples, win-opportunities first; "
            f"{n_priors} candidate prior{prior_s})"
        )

    def on_race_catch_up(self, member: str, position: int, prior_keys: list[ArmKey]) -> None:
        if not prior_keys:
            return
        tags = [self._labels.get(key, key[:6]) for key in prior_keys]
        self._write(f"  {DIM}↻ {member} catch-up #{position}:{RESET} " + ", ".join(tags))

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
        assert arm.slot is not None
        label, idx, total, block = arm.label, arm.slot.idx, arm.slot.total, arm.block
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
        assert scores.slot is not None
        label, total = scores.label, scores.slot.total
        cut = isinstance(scores, Cut) and scores.kind == "eliminated"
        if self._block_racing and fold is not None and arm_elimination(scores) is not None:
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

        if fold is not None and fold.accuracy is not None:
            self._write(self._fmt_round_leader(label, fold.accuracy, fold.composite_fitness))

    def _fmt_round_leader(self, label: str, acc: float, composite: float | None) -> str:
        key = scoreboard_rank_key(composite, acc)
        new_round_max = self._round_best_key is None or key > self._round_best_key
        if new_round_max:
            self._round_best_key = key
            self._round_best_acc = acc
            self._round_best_label = label
            return f"  {GREEN}★ leader: {label} {acc:.1%}{RESET}"
        gap = acc - (self._round_best_acc or acc)
        prior = self._round_best_label or "leader"
        return f"  {DIM}→ {label} {acc:.1%}  ({gap:.1%} from {prior}){RESET}"

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
        verdict, self._verdict = self._verdict, None
        if verdict is not None:
            self._write(
                render_round_verdict(verdict, round_verdict_basis(round_result), series.reading(rn))
            )
        self._write("")
        self._write(_node_top(f"ROUND {rn} SUMMARY{elapsed_label}"))
        table = render_progress_table([self._closed[n] for n in sorted(self._closed)], series)
        for line in table.split("\n"):
            self._write(line)
        for line in self._render_block_lines():
            self._write(_node_line(line))
        self._block_racing, self._block_decided, self._headed = {}, {}, set()
        formula_short = self._anchors.composite_fitness_formula_short
        formula_full = self._anchors.composite_fitness_formula
        fold, election = round_result.fold, round_result.election
        composite = None if fold is None else fold.composite_fitness
        if (
            verdict is None
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
                or (fit := round_result.fit(election.selected)) is None
                or fit.vs_reference is None
                else fit.vs_reference.reference_level("objective"),
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
            round_result.overlap.advance,
            round_result.run_standing.rounds_without_advance,
            self.patience,
        ).split("\n"):
            self._write(line)
        self._write(_node_bottom())
