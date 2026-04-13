"""Notebook phase dispatch + analytics.

Three concerns:
- ``show_*`` analytics functions (progress, leaderboards, axis profiles,
  campaign summary, flip tracking, lineage) — called directly from
  notebook cells and CLI.
- ``_CycleDisplayState`` + SP-diff helpers — mutable state + 3-column
  diff table rendering threaded through phase handlers.
- ``_print_*`` phase handlers + ``_dispatch_phase`` — route PhaseEvent
  objects to per-phase formatters during a feedback cycle.

Imports only from ``notebook_primitives``. The ``NotebookDisplay``
callback class in ``notebook_display`` consumes
``_CycleDisplayState``/``_dispatch_phase``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from promptpotter.application.optimization.phases import PhaseEvent
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.statistics import min_detectable_effect

from .notebook_primitives import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RED,
    RESET,
    YELLOW,
    _dbox_bottom,
    _dbox_line,
    _dbox_sep,
    _dbox_top,
    _node_bottom,
    _node_line,
    _node_top,
    _pp_val,
    _round_rule,
    _scoreboard,
    fmt_pvalue,
)
from .notebook_primitives import (
    _fmt_delta as _fmt_delta_primitive,
)

if TYPE_CHECKING:
    from promptpotter.application.recon.recon_report import ReconBrief


_fmt_delta = _fmt_delta_primitive


# ---------------------------------------------------------------------------
# show_* analytics — called directly from notebook cells and CLI
# ---------------------------------------------------------------------------


def show_progress(campaign_rounds: list, window: int = 8) -> None:
    """Print training-style progress summary after each round."""
    if not campaign_rounds:
        print("No rounds to display.")
        return

    has_composite = any(
        rd.get("composite") is not None and rd.get("composite") != rd["accuracy"]
        for rd in campaign_rounds
    )

    if has_composite:
        print(
            f"\n{'Round':<7s} {'Accuracy':>9s} {'Composite':>10s}"
            f" {'Rolling Avg':>13s} {'Trend':>8s}"
        )
    else:
        print(f"\n{'Round':<7s} {'Accuracy':>9s} {'Rolling Avg':>13s} {'Trend':>8s}")
    accuracies = []

    for rd in campaign_rounds:
        acc = rd["accuracy"]
        accuracies.append(acc)
        n = len(accuracies)

        window_slice = accuracies[-window:]
        rolling_avg = sum(window_slice) / len(window_slice)

        if n <= 1:
            trend_str = "-"
        else:
            delta = acc - accuracies[-2]
            if abs(delta) < 0.001:
                trend_str = "+0.0%  <-- plateau"
            elif delta > 0:
                trend_str = f"+{delta:.1%}"
            else:
                trend_str = f"{delta:.1%}"

        round_label = str(rd["round"])

        if has_composite:
            comp = rd.get("composite", acc)
            print(f"  {round_label:<5s} {acc:>8.1%} {comp:>9.4f} {rolling_avg:>12.1%}  {trend_str}")
        else:
            print(f"  {round_label:<5s} {acc:>8.1%} {rolling_avg:>12.1%}  {trend_str}")

    if len(accuracies) >= 3:
        recent = accuracies[-3:]
        recent_avg = sum(recent) / len(recent)
        if all(abs(a - recent_avg) < 0.005 for a in recent):
            print(
                f"  {YELLOW}-- Plateau: rolling avg stable at {recent_avg:.1%} for 3 rounds{RESET}"
            )


def show_axis_profiles(profiles: list[dict]) -> None:
    """Display axis profiles as a formatted table."""
    if not profiles:
        print("No axis profiles to display.")
        return

    print(f"\n{'Rank':<5s} {'Axis':<25s} {'Type':<15s} {'Card':<5s} {'Range':<8s} {'Budget':<8s}")
    print("-" * 70)
    for rank, p in enumerate(profiles, 1):
        print(
            f"  {rank:<3d} {p['axis']:<25s} {p['axis_type']:<15s} "
            f"{p['cardinality']:<5d} {p['sensitivity_range']:<8.3f} "
            f"{p['exploration_budget']:<8s}"
        )


def show_recon_leaderboard(
    recon_df,
    axis_profiles: list[dict],
) -> None:
    """Display variant leaderboard and per-axis statistics from scan results."""
    import pandas as pd
    from IPython.display import display as ipy_display

    if recon_df.empty:
        print("No scan data to display.")
        return

    sort_col = "composite" if "composite" in recon_df.columns else "accuracy"
    ranked = recon_df.sort_values(sort_col, ascending=False).reset_index(drop=True)

    rows = []
    for i, r in ranked.iterrows():
        label = r["value_preview"]
        if r["delta"] == 0.0:
            label = f"{label} (baseline)"
        rows.append(
            {
                "rank": i + 1,
                "axis": r["axis"],
                "variant": label,
                "accuracy": f"{r['accuracy']:.1%}",
                "delta": f"{r['delta']:+.1%}" if r["delta"] != 0.0 else "-",
                "hits/total": f"{r['hits']}/{r['total']}",
                "errors": int(r.get("errors", 0)),
            }
        )

    print("VARIANT LEADERBOARD (all scan combos)")
    print("=" * 70)
    ipy_display(pd.DataFrame(rows))

    profile_lookup = {p["axis"]: p for p in axis_profiles}
    axis_rows = []
    for axis_name, grp in recon_df.groupby("axis", sort=False):
        prof = profile_lookup.get(axis_name, {})
        accs = grp["accuracy"]
        axis_rows.append(
            {
                "axis": axis_name,
                "type": prof.get("axis_type", grp["axis_type"].iloc[0]),
                "variants": len(grp),
                "mean_acc": f"{accs.mean():.1%}",
                "std_acc": f"{accs.std():.1%}" if len(grp) > 1 else "-",
                "best_acc": f"{accs.max():.1%}",
                "worst_acc": f"{accs.min():.1%}",
                "sensitivity": f"{prof.get('sensitivity_range', accs.max() - accs.min()):.3f}",
                "budget": prof.get("exploration_budget", "?"),
            }
        )

    print("\nPER-AXIS STATISTICS")
    print("=" * 70)
    ipy_display(pd.DataFrame(axis_rows))


def show_recon_query_difficulty(
    store,
    backend_id: str,
):
    """Aggregate per-query hit rates across scan runs and classify difficulty.

    Returns:
        DataFrame sorted by hit_rate ascending (hardest first).
    """
    import pandas as pd
    from IPython.display import display as ipy_display

    summaries = store.dataset_runs.list_all(backend_id)
    scan_summaries = [s for s in summaries if s.get("source") == "run_recon"]

    if not scan_summaries:
        print("No run_recon runs found.")
        return pd.DataFrame()

    query_stats: dict[str, dict] = {}
    for summary in scan_summaries:
        detail = store.dataset_runs.load_by_id(backend_id, summary["run_id"])
        if not detail:
            continue
        for item in detail.get("dataset_run_items", []):
            q = item.get("query", "")
            if not q:
                continue
            if q not in query_stats:
                query_stats[q] = {
                    "ground_truth": item.get("ground_truth", ""),
                    "n_measurements": 0,
                    "n_hits": 0,
                    "n_errors": 0,
                }
            qs = query_stats[q]
            qs["n_measurements"] += 1
            if item.get("hit"):
                qs["n_hits"] += 1
            if is_error_result(item):
                qs["n_errors"] += 1

    if not query_stats:
        print("No query-level data found in scan runs.")
        return pd.DataFrame()

    rows = []
    for query, qs in query_stats.items():
        n = qs["n_measurements"]
        hit_rate = qs["n_hits"] / n if n else 0.0
        error_rate = qs["n_errors"] / n if n else 0.0

        if error_rate == 1.0:
            classification = "error"
        elif hit_rate == 1.0:
            classification = "easy"
        elif hit_rate == 0.0:
            classification = "hard"
        else:
            classification = "discriminating"

        rows.append(
            {
                "query": query[:60],
                "ground_truth": qs["ground_truth"][:50],
                "hit_rate": hit_rate,
                "hits/evals": f"{qs['n_hits']}/{n}",
                "error_rate": error_rate,
                "classification": classification,
            }
        )

    df = pd.DataFrame(rows).sort_values("hit_rate", ascending=True).reset_index(drop=True)

    counts = df["classification"].value_counts()
    total = len(df)
    parts = []
    for cat in ("easy", "discriminating", "hard", "error"):
        c = counts.get(cat, 0)
        parts.append(f"{cat}: {c} ({c / total:.0%})")

    print(f"QUERY DIFFICULTY ({total} queries across {len(scan_summaries)} scan runs)")
    print("=" * 70)
    print(f"  {' | '.join(parts)}")
    print()
    ipy_display(df)

    return df


def format_pipeline_overrides(
    pipeline_params: dict | None,
    pipeline_schema=None,
) -> None:
    """Print pipeline_params as a copy-paste ready ``pipeline_overrides`` dict.

    Output uses nested format: ``{"node_name": {"param": value}}``.
    """
    if not pipeline_params:
        return

    node_entries: list[tuple[str, dict]] = []
    for key, val in pipeline_params.items():
        if key == "steps" or not isinstance(val, dict):
            continue
        tunable = {}
        if pipeline_schema:
            node = pipeline_schema.get_node(key)
            if node:
                tunable = {k: v for k, v in val.items() if k in node.param_keys}
        if not tunable:
            tunable = val
        if tunable:
            node_entries.append((key, tunable))

    if not node_entries:
        return

    print(f"\n  {CYAN}Copy-paste pipeline_overrides:{RESET}")
    print(f"  {DIM}{'─' * 60}{RESET}")
    print('  "pipeline_overrides": {')
    for node_name, params in node_entries:
        print(f'      "{node_name}": {{')
        for param, val in params.items():
            print(f'          "{param}": {val!r},')
        print("      },")
    print("  }")
    print(f"  {DIM}{'─' * 60}{RESET}")


def show_campaign_summary(campaign_rounds: list) -> None:
    """Display campaign comparison table as DataFrame."""
    if not campaign_rounds:
        print("No campaign rounds to display.")
        return

    import pandas as pd
    from IPython.display import display as ipy_display

    rows = []
    for rd in campaign_rounds:
        rows.append(
            {
                "round": rd["round"],
                "label": rd["label"][:40],
                "hit@1": rd["hits"],
                "total": rd["total"],
                "accuracy": f"{rd['accuracy']:.1%}",
                "prompt_id": rd["prompt_fields"].id[:12],
            }
        )

    print(f"CAMPAIGN SUMMARY ({len(campaign_rounds)} rounds)")
    print(f"{'=' * 70}")
    ipy_display(pd.DataFrame(rows))


def show_flip_tracking(campaign_rounds: list) -> None:
    """Compare first vs last round, display per-query flip table."""
    if len(campaign_rounds) < 2:
        print("Need at least 2 rounds for flip tracking.")
        return

    base_r = campaign_rounds[0]["results"]
    final_r = campaign_rounds[-1]["results"]

    if not base_r or not final_r:
        print("Skipping flip tracking — baseline or final results are empty.")
        return

    import pandas as pd
    from IPython.display import display as ipy_display

    flips = []
    for br, fr in zip(base_r, final_r, strict=False):
        b_hit = br["hit"]
        f_hit = fr["hit"]
        if b_hit != f_hit:
            flips.append(
                {
                    "query": br["query"][:50],
                    "flip": "MISS->HIT" if f_hit else "HIT->MISS",
                    "base_pred": br["predicted"][:35],
                    "final_pred": fr["predicted"][:35],
                    "ground_truth": br["ground_truth"][:35],
                }
            )

    gained = sum(1 for f in flips if f["flip"] == "MISS->HIT")
    lost = sum(1 for f in flips if f["flip"] == "HIT->MISS")

    print(f"FLIP TRACKING (baseline -> round {campaign_rounds[-1]['round']})")
    print(f"  Queries gained (MISS->HIT): {gained}")
    print(f"  Queries lost (HIT->MISS):   {lost}")
    print(f"  Net change:                 {gained - lost:+d}")
    print()
    if flips:
        ipy_display(pd.DataFrame(flips))


def show_lineage_chain(campaign_rounds: list) -> None:
    """Display OptSearchPoint lineage chain across rounds."""
    if not campaign_rounds:
        print("No campaign rounds to display.")
        return

    print("LINEAGE CHAIN")
    print("=" * 50)
    for i, rd in enumerate(campaign_rounds):
        ps = rd["prompt_fields"]
        parent = ps.parent_id[:12] if ps.parent_id else "root"
        arrow = "  " if i == 0 else "  -> "
        print(
            f"{arrow}[{ps.id[:12]}] Round {rd['round']}: {rd['label'][:40]} ({rd['accuracy']:.1%})"
        )
        if ps.parent_id:
            print(f"       parent: {parent}  |  changes: {ps.changes_description or 'none'}")


# ---------------------------------------------------------------------------
# Display state — tracks cycle metadata across phase callbacks (display-only)
# ---------------------------------------------------------------------------


@dataclass
class _CycleDisplayState:
    """Mutable display state threaded through phase/callback closures.

    Populated exclusively from PhaseEvent data — never touches services.
    """

    max_rounds: int = 0
    patience: int = 0
    l1_stall_count: int = 0
    round_num: int = 0
    baseline_accuracy: float = 0.0
    baseline_total: int = 0  # sample count for significance tests
    recon_brief: ReconBrief | None = None  # cached for scan reasoning display
    candidates_meta: list = field(default_factory=list)  # from l1_generate exit
    n_scoring_queries: int = 0  # from generate:exit, used in evaluate:enter banner
    current_pipeline_params: dict | None = None  # raw pp for candidate eval callback
    # 3-column SP diff tracking (flattened dot-notation dicts)
    original_sp_flat: dict[str, str] = field(default_factory=dict)
    previous_sp_flat: dict[str, str] = field(default_factory=dict)
    current_sp_flat: dict[str, str] = field(default_factory=dict)
    node_param_keys: dict[str, list[str]] | None = None


# ---------------------------------------------------------------------------
# SP flatten + diff helpers
# ---------------------------------------------------------------------------


def _flatten_sp_summary(
    pp: dict | None,
) -> dict[str, str]:
    """Flatten SearchPoint dimensions into dot-notation display dict.

    - Scalar pipeline params: ``key`` → formatted value
    - JSON Schema params (type=object with properties): expand to
      ``key.field_name`` → description string
    - Mutation-tuple lists: expand ``['+', name, ...]`` to ``key.name`` → desc
    """
    flat: dict[str, str] = {}

    for k, v in (pp or {}).items():
        if k == "steps":
            continue
        if isinstance(v, dict) and v.get("type") == "object" and "properties" in v:
            for prop_name, prop_def in v["properties"].items():
                desc = prop_def.get("description", prop_def.get("type", "?"))
                flat[f"{k}.{prop_name}"] = desc
        elif isinstance(v, list) and v and isinstance(v[0], list):
            for mutation in v:
                if not mutation:
                    continue
                op = mutation[0]
                if op == "+" and len(mutation) >= 5:
                    flat[f"{k}.{mutation[1]}"] = mutation[4]
                elif op == "~" and len(mutation) >= 6:
                    flat[f"{k}.{mutation[2]}"] = mutation[5]
        elif isinstance(v, dict):
            for sub_k, sub_v in v.items():
                flat[sub_k] = _pp_val(sub_v)
        else:
            flat[k] = _pp_val(v)
    return flat


_ABSENT = "-"
_UNCHANGED = "\u00b7"  # middle dot
_VAL_INLINE_MAX = 12  # values longer than this get a lookup code


def _build_candidate_flat(
    parent: dict[str, str],
    candidate_meta: dict,
) -> dict[str, str]:
    """Merge candidate overrides onto parent flat dict.

    When a candidate overrides a schema key, parent's dot-notation
    children for that key are removed first, then the candidate's
    expanded fields are added.
    """
    flat = parent.copy()
    pp = candidate_meta.get("pipeline_params_override")
    if pp:
        for k in pp:
            prefix = f"{k}."
            to_remove = [pk for pk in flat if pk.startswith(prefix)]
            for pk in to_remove:
                del flat[pk]
        override_flat = _flatten_sp_summary(pp)
        flat.update(override_flat)
    return flat


def _group_diff_keys(
    diff_keys: list[str],
    node_param_keys: dict[str, list[str]] | None,
) -> list[tuple[str, list[str]]]:
    """Group diff keys by pipeline node in execution order."""
    if not node_param_keys:
        return [("", diff_keys)]

    key_to_node: dict[str, str] = {}
    for sname, keys in node_param_keys.items():
        for k in keys:
            key_to_node[k] = sname
    for k in diff_keys:
        if k not in key_to_node:
            base = k.split(".")[0]
            if base in key_to_node:
                key_to_node[k] = key_to_node[base]

    groups: dict[str, list[str]] = {sname: [] for sname in node_param_keys}
    groups[""] = []
    for k in diff_keys:
        sname = key_to_node.get(k, "")
        groups.setdefault(sname, []).append(k)

    return [(sname, sorted(keys)) for sname, keys in groups.items() if keys]


def _print_sp_diff(
    columns: list[tuple[str, dict[str, str]]],
    node_param_keys: dict[str, list[str]] | None = None,
    round_num: int | None = None,
) -> None:
    """Print N-column diff table with lookup codes for long values.

    Only rows where at least one column differs are shown. Short values
    (<=12 chars) are shown inline; longer values get a letter code
    ``[a]``..``[z]`` with full text in a legend below the table. When
    ``node_param_keys`` is provided, rows are grouped by pipeline node.
    """
    if len(columns) < 2:
        return

    all_keys: set[str] = set()
    for _, d in columns:
        all_keys.update(d.keys())
    diff_keys = []
    for k in sorted(all_keys):
        vals = [d.get(k) for _, d in columns]
        if len(set(vals)) > 1:
            diff_keys.append(k)
    if not diff_keys:
        return

    lookup: dict[str, str] = {}
    legend: list[tuple[str, str]] = []
    code_idx = 0

    def _get_code(val: str) -> str:
        nonlocal code_idx
        if val in lookup:
            return lookup[val]
        code = chr(ord("a") + code_idx)
        code_idx += 1
        lookup[val] = f"[{code}]"
        legend.append((f"[{code}]", val))
        return f"[{code}]"

    def _cell(val: str | None, prior: str | None) -> str:
        if val is None:
            return _ABSENT
        if val == prior:
            return _UNCHANGED
        if len(val) <= _VAL_INLINE_MAX:
            return val
        return _get_code(val)

    col_w = max(8, max(len(label) for label, _ in columns) + 2)
    max_key = max(len(k) for k in diff_keys)

    r_label = f"Round {round_num}" if round_num is not None else "SPs"
    print(_node_line(f"{CYAN}{r_label} SPs:{RESET}"))
    hdr = f"{'':>{max_key}}  " + "".join(f"{label:<{col_w}}" for label, _ in columns)
    print(_node_line(hdr))

    groups = _group_diff_keys(diff_keys, node_param_keys)
    for _gi, (node_name, group_keys) in enumerate(groups):
        if node_name and len(groups) > 1:
            sep = f"{'─── ' + node_name + ' ':─<{max_key + 2}}"
            print(_node_line(f"{DIM}{sep}{RESET}"))
        for k in group_keys:
            cells = []
            prev_val = None
            for _, d in columns:
                v = d.get(k)
                cells.append(_cell(v, prev_val))
                prev_val = v
            row = f"{k:>{max_key}}  " + "".join(f"{c:<{col_w}}" for c in cells)
            print(_node_line(row))

    if legend:
        print(_node_line(""))
        print(_node_line(f"{CYAN}Values:{RESET}"))
        for code, full in legend:
            print(_node_line(f"  {code} {full}"))


# ---------------------------------------------------------------------------
# Phase handlers — one per (phase, event) pair
# ---------------------------------------------------------------------------


def _print_init_enter(d: dict, state: _CycleDisplayState) -> None:
    config = d["config"]
    dataset = d["dataset"]
    schema = config.pipeline_schema

    state.max_rounds = config.max_rounds or 0
    state.patience = config.l1_patience
    state.original_sp_flat = _flatten_sp_summary(
        schema.to_pipeline_params() if schema else None,
    )
    state.node_param_keys = (
        {s: sorted(k) for s, k in schema.node_param_keys().items()} if schema else None
    )

    model = config.model or "(default)"
    l2 = "enabled" if config.enable_l2 else "disabled"
    l3 = "enabled" if config.enable_l3 else "disabled"
    scan = "YES" if config.recon_brief is not None else "NO"
    sample = config.sp_budget_ttest
    state.baseline_total = sample

    print()
    print(_dbox_top())
    print(_dbox_line(f"{BOLD}FEEDBACK CYCLE STARTING{RESET}"))
    print(_dbox_sep())
    print(
        _dbox_line(f"Max rounds     {state.max_rounds or 999!s:<15s}Patience    {state.patience}")
    )
    print(_dbox_line(f"Candidates     {config.n_variants}"))
    sample_label = f"{sample} of {len(dataset)}"
    mde = min_detectable_effect(sample)
    print(_dbox_line(f"Sample size    {sample_label}"))
    print(_dbox_line(f"Min detectable {YELLOW}\u00b1{mde:.1%}{RESET} (\u03b1=0.05, 80% power)"))
    print(_dbox_line(f"Model          {model}"))
    print(_dbox_line(f"L2 (refine)    {l2:<19s}L3 (plan)   {l3}"))
    print(_dbox_line(f"Scan context   {scan}"))
    critique = "enabled" if config.enable_critique else "disabled"
    print(_dbox_line(f"Critique       {critique}"))
    print(_dbox_bottom())


def _print_init_exit(d: dict, state: _CycleDisplayState) -> None:
    loop_state = d["state"]
    loop_env = d["env"]
    state.baseline_accuracy = loop_state.current_accuracy
    cycle_id = (loop_env.cycle_id or "?")[:12]
    samples = len(loop_env.scoring_dataset)
    obs = "ON" if (loop_env.scoring_ctx and loop_env.scoring_ctx.obs) else "OFF"
    print(
        f"  {GREEN}\u2713{RESET} Initialized  baseline={loop_state.current_accuracy:.1%}  "
        f"cycle={cycle_id}  samples={samples}  obs={obs}"
    )
    crit = loop_state.opt_sp.memory.critique_text
    if crit:
        preview = crit.replace("\n", " ").strip()
        if len(preview) > 80:
            preview = preview[:77] + "..."
        print(f"    {CYAN}Bootstrap critique:{RESET} {preview}")
    resumed = loop_env.resumed_from_round
    if resumed > 0:
        state_parts = []
        critique_chars = len(loop_state.opt_sp.memory.critique_text)
        task_context_keys = len(loop_state.opt_sp.task_context)
        l2_round = loop_state.escalation.l2.round
        if critique_chars:
            state_parts.append(f"critique={critique_chars} chars")
        if task_context_keys:
            state_parts.append(f"task_context={task_context_keys} keys")
        if l2_round:
            state_parts.append(f"l2_round={l2_round}")
        state_suffix = f"  ({', '.join(state_parts)})" if state_parts else ""
        print(f"    Resumed from round {resumed} ({resumed} rounds cached){state_suffix}")
    else:
        print("    Starting fresh (no prior rounds for this cycle)")


def _print_l1_generate_enter(d: dict, state: _CycleDisplayState) -> None:
    acc = d.get("current_accuracy", 0.0)
    preview = d.get("prompt_preview", "").replace("\n", " ").strip()
    if len(preview) > 50:
        preview = preview[:47] + "..."
    if not preview:
        preview = "(no baseline -- seed from scan or provide instruction)"
    n = d.get("n_variants", 0)
    model = d.get("model") or "(default)"
    creativity = d.get("creativity", 0.7)
    scan = "YES" if d.get("has_recon_brief") else "NO"
    crit = "YES" if d.get("has_critique") else "NO"

    new_flat = _flatten_sp_summary(
        d.get("pipeline_params"),
    )
    if state.round_num == 0:
        state.previous_sp_flat = state.original_sp_flat.copy()
    else:
        state.previous_sp_flat = state.current_sp_flat.copy()
    state.current_sp_flat = new_flat

    r_label = f"ROUND {state.round_num + 1}/{state.max_rounds or 999}"
    p_label = f"patience {state.l1_stall_count}/{state.patience}"

    print()
    print(_round_rule(r_label, p_label))

    print()
    print(_node_top("GENERATE"))
    print(_node_line(f"Current best    {acc:.1%}"))
    print(_node_line(f"Prompt          {preview}"))
    print(
        _node_line(
            f"Candidates      {n}   Creativity: {creativity}   Scan: {scan}   Critique: {crit}"
        )
    )
    print(_node_line(f"Model           {model}"))

    if state.recon_brief and d.get("has_recon_brief"):
        axes = state.recon_brief.improving_axes
        bl_acc = state.recon_brief.baseline_accuracy
        if axes:
            print(
                _node_line(
                    f"{CYAN}Scan focus:{RESET} {len(axes)} improving axes [{', '.join(axes)}]"
                )
            )
        if bl_acc > 0:
            print(_node_line(f"{CYAN}Scan baseline:{RESET} {bl_acc:.1%}"))

    print(_node_bottom())


def _print_l1_generate_exit(d: dict, state: _CycleDisplayState) -> None:
    n = d.get("n_candidates", 0)
    source = "loaded from disk" if d.get("loaded_from_disk") else "from LLM"
    state.n_scoring_queries = d.get("n_scoring_queries", 0)
    state.candidates_meta = d.get("candidates", [])

    print(f"  {GREEN}✓{RESET} {n} candidates generated ({source})")

    columns: list[tuple[str, dict[str, str]]] = [
        ("Start", state.original_sp_flat),
        ("Parent", state.current_sp_flat),
    ]
    for c in state.candidates_meta:
        c_flat = _build_candidate_flat(state.current_sp_flat, c)
        columns.append((f"C{c['idx'] + 1}", c_flat))
    print()
    _print_sp_diff(
        columns,
        node_param_keys=state.node_param_keys,
        round_num=state.round_num + 1,
    )


def _print_l1_score_enter(d: dict, state: _CycleDisplayState) -> None:
    n_cand = d.get("n_candidates", 0)
    n_q = d.get("n_queries", 0)
    n_calls = n_cand * n_q
    calls_label = f"{n_cand} \u00d7 {n_q} = {n_calls} calls"

    scoring_pp = d.get("current_pipeline_params")
    if scoring_pp is not None:
        state.current_pipeline_params = scoring_pp

    print()
    print(_node_top("EVALUATE", calls_label))


def _print_l1_score_exit(d: dict, state: _CycleDisplayState) -> None:
    from promptpotter.shared.statistics import proportion_test

    w_acc = d.get("winner_accuracy", 0.0)
    w_comp = d.get("winner_composite")
    improved = d.get("improved", False)
    action = d.get("next_action", "?")
    scores = d.get("candidate_scores", [])

    for i, s in enumerate(scores):
        s["label"] = f"C{i + 1}"
    non_aborted = [s for s in scores if not s.get("escalation_aborted")]
    best_s = (
        max(non_aborted, key=lambda s: (s.get("composite", s["accuracy"]), s["accuracy"]))
        if non_aborted
        else {}
    )
    winner = best_s.get("label", "?")

    if len(scores) > 3:
        board = _scoreboard(scores, winner, state.baseline_accuracy)
        if board:
            print(board)
    elif scores:
        _ranked = sorted(
            scores, key=lambda s: (s.get("composite", s["accuracy"]), s["accuracy"]), reverse=True
        )
        parts = []
        for s in _ranked:
            lbl = s.get("label", "?")
            acc = s["accuracy"]
            ab = " (aborted)" if s.get("escalation_aborted") else ""
            parts.append(f"{lbl}={acc:.1%}{ab}")
        print(f"  Scoreboard: {' | '.join(parts)}")

    comp_tag = ""
    if w_comp is not None and w_comp != w_acc:
        comp_tag = f"  composite={w_comp:.4f}"

    winner_hits = best_s.get("hits", 0)
    winner_total = best_s.get("total", 0)

    if improved:
        delta = w_acc - state.baseline_accuracy
        bl_hits = round(state.baseline_accuracy * winner_total)
        p = proportion_test(winner_hits, winner_total, bl_hits, winner_total)
        sig_tag = f"  {fmt_pvalue(p)}"
        print(
            f"  {GREEN}{BOLD}✓ IMPROVED{RESET}  {w_acc:.1%}"
            f" (was {state.baseline_accuracy:.1%},"
            f" {_fmt_delta(delta)}){comp_tag}{sig_tag}"
            f"  ->  next: {action}"
        )
        state.baseline_accuracy = w_acc
    else:
        print(f"  {YELLOW}{BOLD}⚠ NO IMPROVEMENT{RESET}  best candidate {w_acc:.1%}{comp_tag}")

    crit = d.get("critique_text", "")
    if crit:
        crit_text = crit.replace("\n", " ").strip()
        print(f"  {CYAN}Critique:{RESET} {crit_text}")


def _print_escalation_enter(d: dict, state: _CycleDisplayState) -> None:
    check = d.get("check_name", "?")
    target = d.get("target", "?")
    rate = d.get("degraded_rate", 0)
    wtypes = d.get("warning_types", {})

    print()
    print(_node_top("ESCALATION", f"{check} \u2192 {target}"))
    print(_node_line(f"{YELLOW}Degraded: {rate:.0%} of queries{RESET}"))
    for wt, count in wtypes.items():
        print(_node_line(f"{wt}: {count} occurrences"))
    print(_node_bottom())


def _print_escalation_exit(d: dict, state: _CycleDisplayState) -> None:
    classifications = d.get("classifications", [])
    if classifications:
        print(f"  {CYAN}Warning classifications:{RESET}")
        for c in classifications:
            print(f"    {c['warning_type']}: {c['status']}")


def _print_refine_enter(d: dict, state: _CycleDisplayState) -> None:
    l2r = d.get("l2_round", "?")
    stalls = d.get("l1_stall_count", "?")
    acc = d.get("current_accuracy", 0.0)
    best = d.get("best_accuracy", 0.0)
    params = d.get("current_params", {})

    print()
    print(_node_top("L2 REFINE CONTEXT", f"L2 round {l2r}"))
    print(_node_line(f"L1 stalled {stalls} rounds  |  acc={acc:.1%}  best={best:.1%}"))
    if params:
        parts = []
        for k, v in list(params.items())[:5]:
            vs = str(v)
            if len(vs) > 30:
                vs = vs[:27] + "..."
            parts.append(f"{k}={vs}")
        extra = len(params) - 5
        params_str = ", ".join(parts)
        if extra > 0:
            params_str += f", +{extra} more"
        print(_node_line(f"Current params: {params_str}"))
    else:
        print(_node_line("Current params: (none)"))
    print(_node_line("LLM analyzing failure patterns..."))
    print(_node_bottom())


def _print_refine_exit(d: dict, state: _CycleDisplayState) -> None:
    import json

    n_changes = d.get("param_changes_count", 0)
    tc = f", {GREEN}task_context updated{RESET}" if d.get("task_context_changed") else ""
    _action = d.get("action", "continue")
    probe = f", {CYAN}action={_action}{RESET}" if _action != "continue" else ""
    desc = d.get("changes_description", "")
    print(f"  {GREEN}✓{RESET} L2 decision: {n_changes} param changes{tc}{probe}")
    if desc:
        print(f"    {desc}")

    warned = d.get("warned_queries", 0)
    top_w = d.get("top_warning", "")
    if warned:
        print(f"    {YELLOW}⚠ {warned} queries with recurring pipeline warnings ({top_w}){RESET}")

    l2_prompt = d.get("l2_prompt", "")
    if l2_prompt:
        print(f"\n  {CYAN}--- L2 PROMPT (sent to LLM) ---{RESET}")
        for line in l2_prompt.split("\n"):
            print(f"  {CYAN}│{RESET} {line}")
        print(f"  {CYAN}--- END PROMPT ---{RESET}")

    l2_resp = d.get("l2_response")
    if l2_resp:
        print(f"\n  {CYAN}--- L2 RESPONSE (raw JSON) ---{RESET}")
        formatted = json.dumps(l2_resp, indent=2)
        for line in formatted.split("\n")[:40]:
            print(f"  {CYAN}│{RESET} {line}")
        print(f"  {CYAN}--- END RESPONSE ---{RESET}")


def _print_probe_enter(d: dict, state: _CycleDisplayState) -> None:
    n = d.get("n_probe_queries", 0)
    queries = d.get("probe_queries", [])
    print()
    print(_node_top("PROBE ROUND", f"{n} queries"))
    print(_node_line("Testing warned queries with new settings..."))
    for q in queries[:5]:
        print(_node_line(f"  {q[:70]}"))
    if len(queries) > 5:
        print(_node_line(f"  ... +{len(queries) - 5} more"))
    print(_node_bottom())


def _print_probe_exit(d: dict, state: _CycleDisplayState) -> None:
    n = d.get("n_probed", 0)
    hits = d.get("probe_hits", 0)
    if n:
        rate = hits / n
        color = GREEN if rate > 0.5 else YELLOW
        print(f"  {color}⚡ Probe: {hits}/{n} hits ({rate:.0%}){RESET}")
    else:
        print(f"  {YELLOW}⚡ Probe: no matching queries found{RESET}")


def _print_plan_enter(d: dict, state: _CycleDisplayState) -> None:
    l3r = d.get("l3_round", "?")
    l2_stalls = d.get("l2_stall_count", "?")
    plan = d.get("current_plan_preview", "")
    if len(plan) > 55:
        plan = plan[:52] + "..."

    print()
    print(_node_top("L3 MODIFY PLAN", f"L3 round {l3r}"))
    print(_node_line(f"L2 stalled {l2_stalls} rounds"))
    print(_node_line(f"Current plan: {plan}"))
    print(_node_line("LLM designing new strategy..."))
    print(_node_bottom())


def _print_plan_exit(d: dict, state: _CycleDisplayState) -> None:
    new_plan = d.get("new_plan_preview", "")
    if len(new_plan) > 55:
        new_plan = new_plan[:52] + "..."
    desc = d.get("changes_description", "")
    print(f"  {GREEN}✓{RESET} New plan: {new_plan}")
    if desc:
        print(f"    {desc}")


def _print_backend_warning(d: dict, state: _CycleDisplayState) -> None:
    msg = d.get("message", "")
    advice = d.get("advice", "")
    resets = d.get("degradation_reset_count", 0)
    steps = d.get("problem_steps", [])
    wtypes = d.get("persistent_warning_types", {})

    print()
    print(_dbox_top())
    print(_dbox_line(f"{RED}{BOLD}BACKEND WARNING{RESET}"))
    print(_dbox_sep())
    print(_dbox_line(msg))
    print(_dbox_line(""))
    print(_dbox_line(advice))
    print(_dbox_sep())
    steps_str = ", ".join(steps) if steps else "unknown"
    print(_dbox_line(f"Resets: {resets}  |  Steps: {steps_str}"))
    for wt, count in wtypes.items():
        print(_dbox_line(f"  {wt}: {count} occurrences"))
    print(_dbox_bottom())


# ---------------------------------------------------------------------------
# Phase dispatch
# ---------------------------------------------------------------------------

_PHASE_HANDLERS: dict[str, Callable] = {
    "init:enter": _print_init_enter,
    "init:exit": _print_init_exit,
    "l1_generate:enter": _print_l1_generate_enter,
    "l1_generate:exit": _print_l1_generate_exit,
    "l1_score:enter": _print_l1_score_enter,
    "l1_score:exit": _print_l1_score_exit,
    "refine_strategy:enter": _print_refine_enter,
    "refine_strategy:exit": _print_refine_exit,
    "modify_plan:enter": _print_plan_enter,
    "modify_plan:exit": _print_plan_exit,
    "escalation:enter": _print_escalation_enter,
    "escalation:exit": _print_escalation_exit,
    "probe_round:enter": _print_probe_enter,
    "probe_round:exit": _print_probe_exit,
    "backend_warning:notify": _print_backend_warning,
}


def _dispatch_phase(event: PhaseEvent, state: _CycleDisplayState) -> None:
    """Route a PhaseEvent to its phase-specific formatter."""
    if event.round is not None:
        state.round_num = event.round
    key = f"{event.phase}:{event.event}"
    handler = _PHASE_HANDLERS.get(key)
    if handler:
        handler(event.data, state)
    else:
        print(f"  [{event.phase.upper()} {event.event}] {event.data}")
