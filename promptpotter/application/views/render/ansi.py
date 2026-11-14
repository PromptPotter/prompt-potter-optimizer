from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import assert_never

from promptpotter.application.views.render.primitives import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RESET,
    YELLOW,
    _fmt_delta,
    _node_block,
    _node_line,
    _node_top,
    _round_rule,
    _scoreboard,
    fmt_coverage,
    fmt_fitness,
)
from promptpotter.domain.arm_reading import DisplayMetric, fold_fitness_text
from promptpotter.domain.arms import ArmState, Concluded
from promptpotter.domain.candidate_diff import (
    build_candidate_flat,
    flatten_prompts,
    flatten_sp_summary,
    group_diff_keys,
)
from promptpotter.domain.paired_reading import READING_STATE_INFO, level_text
from promptpotter.domain.phase_acts import (
    CellFormula,
    InitClosed,
    InitOpened,
    MeasureOpened,
    PassOpened,
    ProposeClosed,
    ProposeOpened,
    StepClosed,
    StepFact,
    StepOpened,
)
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.records.held_out import BenchScoredRecord, PassGradedRecord
from promptpotter.domain.records.run_records import RoundStatement
from promptpotter.domain.ruler import THETA_CAVEAT_INFO, SeriesReading, ThetaCaveat
from promptpotter.domain.spend import RATE_PRICED_LABEL, StopSpend, usd_text
from promptpotter.domain.verification import NoiseBand, VerifyReading
from promptpotter.domain.wounds import COLLAPSE_WORDS, collapse_counts, collapse_reason
from promptpotter.shared.answer_text import truncate
from promptpotter.shared.composite import render_composite_fitness_block


def _render_init_enter(v: InitOpened) -> str:
    if not v.warnings:
        return ""
    out = [""]
    for w in v.warnings:
        out.append(f"{YELLOW}⚠ {BOLD}{w.title}{RESET}")
        out.append(f"    {YELLOW}{w.detail}{RESET}")
    return "\n".join(out)


def _render_init_exit(v: InitClosed) -> str:
    out = [
        f"  {GREEN}✓{RESET} Initialized  origin fitness={fmt_fitness(v.origin_fitness)}  "
        f"cycle={v.cycle_id[:12]}  samples={v.samples}  bench={v.bench_samples} held out"
    ]
    suffix = f"  (task_context={v.framing_keys} keys)" if v.framing_keys else ""
    if v.cached_rounds > 0:
        out.append(
            f"    Resuming round {v.resumed_from_round} "
            f"({v.cached_rounds} prior rounds cached){suffix}"
        )
    else:
        tail = f"  — starting at round {v.resumed_from_round}{suffix}"
        out.append(f"    Starting fresh (no prior rounds for this cycle){tail}")
    return "\n".join(out)


def render_round_start(
    v: ProposeOpened, round_num: int | None, parent: RoundStatement, schema: PipelineSchema
) -> str:
    """*parent* is the round the parent last ended (``parent_round``): its head and its level."""
    standing = v.run_standing
    round_label = (
        f"ROUND {round_num}  {standing.lives.bar}"
        if standing is not None and standing.lives is not None
        else f"ROUND {round_num}"
        if v.budget_rounds is None
        else f"ROUND {round_num}/{v.budget_rounds}"
    )
    arms = "?" if v.arms is None else str(v.arms)
    # The header renders before candidates exist: it describes the parent.
    head = _head(parent).individual
    preview = "\n\n".join(head.render(node) for node in schema.prompt_nodes()).replace("\n", " ")
    preview = truncate(preview.strip(), 50, "...") or "(empty)"
    fitness = None if parent.fold is None else parent.fold.fitness
    return "\n".join(
        [
            "",
            _round_rule(round_label, v.standing),
            "",
            _node_block(
                "GENERATE",
                f"Parent fitness  {fmt_fitness(fitness)}",
                f"Parent prompt   {preview}",
                f"Candidates      {arms}" + (f"   {v.note}" if v.note else ""),
                f"Model           {v.model or '(default)'}",
            ),
        ]
    )


def render_population(
    v: ProposeClosed,
    round_num: int | None,
    *,
    origin: RoundStatement,
    parent: RoundStatement,
    arms: Sequence[ArmState],
    schema: PipelineSchema,
) -> str:
    """Each proposal (an arm of *arms*, the round's announced, slotted this round) a column beside
    the heads of *origin* and *parent*."""
    src = "loaded from disk" if v.replayed else "from LLM"
    arms = [arm for arm in arms if arm.slot is not None and arm.slot.round == round_num]
    parent_flat = _flat(_head(parent))
    columns = [("Start", _flat(_head(origin))), ("Parent", parent_flat)]
    columns.extend(
        (
            arm.label,
            build_candidate_flat(
                parent_flat,
                pipeline_overlay=arm.overlay,
                prompt_flat=flatten_prompts(arm.individual.prompts),
            ),
        )
        for arm in arms
    )
    return "\n".join(
        [
            f"  {GREEN}✓{RESET} {len(arms)} candidates generated ({src})",
            "",
            render_sp_diff(
                columns,
                round_num,
                proposer=v.proposer,
                node_param_keys={s: sorted(k) for s, k in schema.node_param_keys().items()},
                collapses=collapse_counts(arm.validation_failures for arm in arms),
            ),
        ]
    )


def _head(round_: RoundStatement) -> Concluded:
    head = round_.head
    assert head is not None, f"round {round_.round} elected: it ends on an arm"
    return head


def _flat(arm: Concluded) -> dict[str, str]:
    """One subject's diff column: its resolved node params, then its prompts."""
    laid = PipelineParams.model_validate(arm.resolved_params).laid_nodes
    return flatten_sp_summary(laid) | flatten_prompts(arm.individual.prompts)


def _render_measure_enter(v: MeasureOpened) -> str:
    return "\n" + _node_top("MEASURE", f"{v.candidates} candidates on {v.cells} cells")


def render_round_verdict(
    v: RoundStatement,
    basis: Sequence[str],
    level: SeriesReading | None,
    *,
    cell_formula: CellFormula | None,
    display_metric: DisplayMetric,
) -> str:
    """Never re-chosen by a point estimate here, which could name another arm than the one kept."""
    # Less the proposals an invariant collapsed: they burned no call.
    arms = [
        row.reading
        for row in v.ranked()
        if collapse_reason(v.arms[row.reading.arm.key].validation_failures) is None
    ]
    out: list[str] = [""]
    if board := _scoreboard(arms):
        out.append(board)

    short = None if cell_formula is None else cell_formula.short
    formula = short or (None if cell_formula is None else cell_formula.formula)
    show_inline = not formula
    fold = v.fold
    fitness = None if fold is None else fold.fitness
    composite = None if fold is None else fold.composite_fitness
    comp_tag = (
        f"  composite_fitness={composite:.4f}"
        if show_inline and composite is not None and composite != fitness
        else ""
    )

    fitness_txt = fold_fitness_text(fold)
    # A cold θ is logit-fitness, so the headline stays fitness.
    theta = (
        level.reading.theta
        if display_metric == "ability" and level is not None and not level.reading.ruler.cold
        else None
    )
    headline = fitness_txt if theta is None else f"θ {theta:+.3f}"
    detail = [] if theta is None else [fitness_txt]

    selected = next((s.vs_reference for s in arms if s.election.selected), None)
    if v.improved:
        # An arm that stopped short gets no reference rate: over a prefix it is a lift nobody measured.
        lift = selected.on_whole_set if selected else None
        detail.append(
            f"vs reference {level_text(lift.rate_a, lift.measurand.unit)}, {_fmt_delta(lift)}"
            if lift is not None
            else "no matched reference — stopped before covering its reference's cells"
        )
        ended_on = "" if (head := v.head) is None else head.label
        out.append(
            f"  {GREEN}{BOLD}✓ SELECTED {ended_on}{RESET}  {headline}"
            f" ({', '.join(detail)}){comp_tag}"
        )
    else:
        detail += ["the best-so-far held", f"n={0 if fold is None else fold.total}"]
        out.append(f"  {YELLOW}{BOLD}· HELD{RESET}  {headline} ({', '.join(detail)}){comp_tag}")
    if (election := v.election) is not None and election.verdict_reason:
        out.append(f"  {DIM}why: {election.verdict_reason}{RESET}")
    # Every number above still renders under a SILENT caveat: this line is the terminal's only notice.
    caveats: dict[ThetaCaveat, list[str]] = {}
    if level is not None and level.caveat is not None:
        caveats[level.caveat] = []
    for arm in arms:
        if arm.ability is not None and arm.ability.caveat is not None:
            caveats.setdefault(arm.ability.caveat, []).append(arm.arm.label)
    for caveat, labels in caveats.items():
        scope = f" ({', '.join(labels)})" if labels else ""
        out.append(
            f"  {YELLOW}⚠ θ caveat{scope}: {THETA_CAVEAT_INFO[caveat].label}{RESET}"
            f" {DIM}[{caveat.value}]{RESET}"
        )
    out.extend(f"  {line}" for line in basis)

    if not show_inline and fold is not None and composite is not None:
        for line in render_composite_fitness_block(
            composite,
            fold.evaluators,
            formula,
            reference=selected.reference_level("objective") if selected else None,
            use_short_names=bool(short),
        ):
            out.append(f"  {line}")
    return "\n".join(out)


def _fact_lines(facts: Mapping[str, StepFact]) -> list[str]:
    """Each said fact on its own line, cut to the box; an unmeasured one is not said."""
    said = {name: value for name, value in facts.items() if value is not None}
    width = max(map(len, said), default=0)
    texts = {k: fmt_fitness(v) if isinstance(v, float) else str(v) for k, v in said.items()}
    return [
        f"{k:<{width}}  {truncate(t.replace(chr(10), ' '), 60, '...')}" for k, t in texts.items()
    ]


def _render_step_enter(phase: str, v: StepOpened) -> str:
    title = phase.replace("_", " ").upper()
    return "\n" + _node_block(title, *_fact_lines(v.facts), f"{v.activity}...", label_right=v.node)


def _render_step_exit(phase: str, v: StepClosed) -> str:
    if not v.facts:
        return ""
    out = [
        f"  {GREEN}✓{RESET} {phase.replace('_', ' ')}",
        *(f"    {line}" for line in _fact_lines(v.facts)),
    ]
    if v.audit is not None:
        out.append(
            f"  {CYAN}{v.audit} call{RESET} {DIM}→ GET …/rounds/N/audit"
            f"::nodes.{v.audit} (prompt · response · usage){RESET}"
        )
    return "\n".join(out)


def to_text(
    phase: str, act: InitOpened | InitClosed | MeasureOpened | PassOpened | StepOpened | StepClosed
) -> str:
    """A propose act reads the round's arms besides (`render_round_start`, `render_population`)."""
    match act:
        case InitOpened():
            return _render_init_enter(act)
        case InitClosed():
            return _render_init_exit(act)
        case MeasureOpened():
            return _render_measure_enter(act)
        case StepOpened():
            return _render_step_enter(phase, act)
        case StepClosed():
            return _render_step_exit(phase, act)
        case PassOpened():
            return _render_pass_enter(act)
        case _:
            assert_never(act)


def render_run_spend(v: StopSpend) -> str:
    spent = v.spent
    bits = [
        f"billed {usd_text(spent.billed_usd)} (what the provider reported it charged)",
        f"{RATE_PRICED_LABEL} {usd_text(spent.rate_priced_usd)} (calls no provider reported a charge for)",
        f"incurred {usd_text(spent.incurred_usd)} (every cell priced, replays included)",
    ]
    bits.extend(v.meter_lines())
    return f"  {DIM}spend: {' · '.join(bits)}{RESET}"


def _render_pass_enter(v: PassOpened) -> str:
    if v.verification == "verify":
        return f"  {DIM}verify {v.arm.label}: {v.rows} unseen cells, {v.strategy}{RESET}"
    subject = "the origin" if v.subject == "origin" else f"the R{v.arm.round} selection"
    return f"  {DIM}bench pass: {subject} on {v.rows} held-out rows{RESET}"


def render_pass_graded(v: PassGradedRecord) -> str:
    reading = v.reading
    if isinstance(reading, VerifyReading):
        return _render_verify_graded(reading)
    if isinstance(reading, NoiseBand):
        return f"  {DIM}{render_noise_band(v)}{RESET}"
    if v.refusal is not None:
        return f"  {YELLOW}verify {v.verification.arm.label}: not sent — {v.refusal}{RESET}"
    if reading is None:
        cut = v.verification.stop
        stop = "" if cut is None else f": {cut.cause.value}"
        return (
            f"  {YELLOW}{v.verification.kind}: no reading — "
            f"{READING_STATE_INFO[v.state].label}{stop}{RESET}"
        )
    level = reading.level
    value = "—" if level is None else f"{level.value:.3f}"
    return (
        f"  {DIM}bench R{reading.round}: {reading.headline} {value} on "
        f"{reading.n} held-out rows{RESET}"
    )


def render_noise_band(v: PassGradedRecord) -> str:
    band = v.reading
    assert isinstance(band, NoiseBand), "a noise-floor pass reads a band"
    recorded = band.recorded.composite
    raw = ", ".join(f"{c:.4f}" for c in band.raw)
    return (
        f"noise floor {v.verification.arm.label}: k={len(band.raw)} composite "
        f"{band.band.text} on {len(v.verification.sample_keys)} rows (raw: {raw}); recorded on "
        f"the same rows {'—' if recorded is None else recorded.point}"
    )


def _render_verify_graded(r: VerifyReading) -> str:
    levels = " → ".join(
        "—" if (level := side.fitness) is None else level.point for side in (r.recorded, r.fresh)
    )
    verdict = {True: " — held", False: f" — {YELLOW}dropped{RESET}{DIM}", None: ""}[r.held]
    lift = ""
    pair = r.vs_origin
    if (paired := pair.headline) is not None and pair.coverage is not None:
        lift = f", lift over C0 {paired.estimate.text} on {fmt_coverage(pair.coverage)}"
    return (
        f"  {DIM}verify {r.label}: fitness {levels} on {r.fresh.n} unseen cells"
        f"{lift}{verdict}{RESET}"
    )


def render_bench_scored(v: BenchScoredRecord) -> str:
    bench = v.bench
    column = bench.headline
    graded = bench.graded
    if graded is None:
        # Loud only where a pass or the run broke; a bench still waiting is no fault.
        info = READING_STATE_INFO[bench.status.state]
        return f"  {YELLOW if info.fault else DIM}bench: no headline — {info.sentence}{RESET}"
    selected = graded.selected
    levels = " → ".join(
        "—" if (level := reading.of(column)) is None else level.point for reading in graded
    )
    band = ""
    if (lift := bench.lift(column)) is not None:
        band = f", lift {lift.estimate.text}"
    return (
        f"  bench: {column} {levels} (origin → R{selected.round} selection, "
        f"{selected.n} held-out rows{band})"
    )


_SP_DIFF_ABSENT = "-"
_SP_DIFF_UNCHANGED = "·"
_SP_DIFF_VAL_INLINE_MAX = 12


def render_sp_diff(
    columns_in: Sequence[tuple[str, dict[str, str]]],
    round_num: int | None,
    *,
    proposer: str,
    node_param_keys: dict[str, list[str]] | None,
    collapses: Mapping[str, int],
) -> str:
    """Each column a flat ``key → value`` of one subject: the start, the parent, then each candidate."""
    if len(columns_in) < 2:
        return ""

    parent = columns_in[1][1]
    clone_labels = {label for label, flat in columns_in[2:] if flat == parent}
    columns: list[tuple[str, dict[str, str]]] = [
        (
            f"{label}[clone]" if label in clone_labels else label,
            flat,
        )
        for label, flat in columns_in
    ]

    warning_lines: list[str] = []
    bits = [f"{collapses[r]} {w}" for r, w in COLLAPSE_WORDS.items() if collapses.get(r)]
    if bits:
        n_total = sum(1 for label, _ in columns_in if label.startswith("C"))
        n_valid = max(0, n_total - sum(collapses.values()))
        cl_text = f" ({', '.join(sorted(clone_labels))})" if clone_labels else ""
        warning_lines.append(
            _node_line(
                f"{YELLOW}⚠ {proposer} produced {' / '.join(bits)} variant(s){cl_text} — "
                f"synthetic-zeroed (no API cost). yield={n_valid / n_total:.0%} "
                f"({n_valid}/{n_total} valid).{RESET}"
            )
        )

    all_keys = {k for _, d in columns for k in d}
    diff_keys = sorted(k for k in all_keys if len({d.get(k) for _, d in columns}) > 1)
    if not diff_keys:
        return "\n".join(warning_lines) if warning_lines else ""

    lookup: dict[str, str] = {}
    # Keyed by VALUE, so one prompt shared by Start/Parent/candidates stays a single legend row.
    legend: dict[str, tuple[int, set[str], list[str]]] = {}
    code_idx = 0

    def _get_code(val: str, key: str, column: str) -> str:
        nonlocal code_idx
        code = lookup.get(val)
        if code is None:
            code = f"[{chr(ord('a') + code_idx)}]"
            code_idx += 1
            lookup[val] = code
            legend[code] = (len(val), set(), [])
        _, keys, cols = legend[code]
        keys.add(key)
        if column not in cols:
            cols.append(column)
        return code

    def _cell(val: str | None, prior: str | None, key: str, column: str) -> str:
        if val is None:
            return _SP_DIFF_ABSENT
        if val == prior:
            return _SP_DIFF_UNCHANGED
        if len(val) <= _SP_DIFF_VAL_INLINE_MAX:
            return val
        return _get_code(val, key, column)

    max_key = max(len(k) for k in diff_keys)

    groups = group_diff_keys(diff_keys, node_param_keys)
    rendered_groups: list[tuple[str, list[tuple[str, list[str]]]]] = []
    for node_name, group_keys in groups:
        rows: list[tuple[str, list[str]]] = []
        for k in group_keys:
            cells: list[str] = []
            start_val = columns[0][1].get(k) if columns else None
            parent_val = columns[1][1].get(k) if len(columns) > 1 else None
            for ci, (_, d) in enumerate(columns):
                v = d.get(k)
                if ci == 0:
                    prior: str | None = None
                elif ci == 1:
                    prior = start_val
                else:
                    prior = parent_val
                cells.append(_cell(v, prior, k, columns[ci][0]))
            rows.append((k, cells))
        rendered_groups.append((node_name, rows))

    n_cols = len(columns)
    col_w: list[int] = []
    for ci in range(n_cols):
        label_w = len(columns[ci][0])
        cell_w = max(
            (len(cells[ci]) for _, rows in rendered_groups for _, cells in rows),
            default=0,
        )
        col_w.append(max(label_w, cell_w) + 2)

    out: list[str] = list(warning_lines)
    r_label = f"Round {round_num}" if round_num is not None else "SPs"
    out.append(_node_line(f"{CYAN}{r_label} SPs:{RESET}"))
    hdr = f"{'':>{max_key}}  " + "".join(
        f"{label:<{col_w[ci]}}" for ci, (label, _) in enumerate(columns)
    )
    out.append(_node_line(hdr))

    for node_name, rows in rendered_groups:
        if len(rendered_groups) > 1:
            sep_name = node_name or "prompt"
            sep = f"{'─── ' + sep_name + ' ':─<{max_key + 2}}"
            out.append(_node_line(f"{DIM}{sep}{RESET}"))
        for k, cells in rows:
            row = f"{k:>{max_key}}  " + "".join(f"{c:<{col_w[ci]}}" for ci, c in enumerate(cells))
            out.append(_node_line(row))

    if legend:
        rf = f"round_{round_num:04d}.json" if round_num is not None else "the round file"
        out.append(_node_line(""))
        out.append(
            _node_line(
                f"{CYAN}Values{RESET} {DIM}— full text in {rf}"
                f"::arms{{}}.individual.prompts / .pipeline_params, joined on label{RESET}"
            )
        )
        key_w = max(len(" ".join(sorted(keys))) for _, keys, _ in legend.values())
        for code, (n_bytes, keys, cols) in legend.items():
            names = " ".join(sorted(keys))
            out.append(
                _node_line(
                    f"  {code} {names:<{key_w}}  {n_bytes:>7,} B  {DIM}{' '.join(cols)}{RESET}"
                )
            )

    return "\n".join(out)
