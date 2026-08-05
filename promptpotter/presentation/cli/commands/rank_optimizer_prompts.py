"""``cmd_rank_optimizer_prompts`` — rank every optimizer prompt state on disk by paired candidate-minus-origin effect.

Read-only and **zero spend**: :func:`rank_optimizer_prompts` re-derives the ranking from round files
already written, so it pools every cell every past L4 run paid for and answers "which edit
actually held up" retroactively. A verb as well as ``GET /optimizer-prompt-ranking`` because a
CLI-only operator has no route to HTTP.

It NAMES a winner and writes nothing. Graduating one into
``promptpotter/assets/optimizer/pipeline.yaml`` stays a deliberate hand-edit — that manifest is
operator-owned, and no verb here writes it.
"""

from __future__ import annotations

import argparse
import logging

from promptpotter.application.optimizer_prompt_ranking import OuterSnr, rank_optimizer_prompts
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    get_verbose,
    identity_from_args,
)

logger = logging.getLogger("promptpotter.presentation.cli")


def _snr_lines(snr: OuterSnr) -> list[str]:
    """Whether the ranking above can be believed at all, in three lines or one.

    A ranking with no resolving power still prints a leader, and reading that leader as a
    finding is the error this block exists to stop. Teaches the number rather than dumping it:
    the operator's next decision is sharpen-the-cells versus buy-more-cells, and the comparison
    that decides it is `n_cells_to_verdict` against the panel actually run."""
    if snr.within_sd is None or snr.between_sd is None:
        need = (
            "no state measured twice on the same cell"
            if snr.within_sd is None
            else "only one state"
        )
        return [
            "",
            f"Resolving power: UNKNOWN — the corpus has {need}, so nothing here separates a "
            "real difference between these arms from the spread of re-reading one of them. "
            "Treat the order above as a diagnostic, not a result.",
        ]
    out = [
        "",
        f"Resolving power: re-reading ONE arm moves it {snr.within_sd:.4f} "
        f"({snr.within_n_groups} repeated cell(s)); the arms above differ by "
        f"{snr.between_sd:.4f} across {snr.between_n_states} state(s).",
    ]
    if snr.n_cells_to_verdict is not None:
        out.append(
            f"At that noise and that contrast a verdict needs ~{snr.n_cells_to_verdict} cells. "
            "Under your panel size, the ranking is a behavioural diagnostic, not a selector — "
            "above it, a leader is a finding."
        )
    return out


async def cmd_rank_optimizer_prompts(args: argparse.Namespace) -> CommandResult:
    """Rank the optimizer prompt corpus; print the top ``--top`` states with their CIs."""
    from promptpotter.config.logging import setup_logging

    setup_logging(style="full" if get_verbose() else "cli")
    registry = rank_optimizer_prompts(
        build_stores(identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT)
    )

    if not registry.candidates:
        return CommandResult(
            data=registry.model_dump(),
            human=(
                f"{registry.n_cycles_scanned} self-optimizing campaign(s) scanned, no scored "
                "optimizer-prompt edits on disk. An edit needs its campaign's round-0 origin "
                "runs and at least one later round to compare against."
            ),
        )

    lines = [
        f"{len(registry.candidates)} edit(s) to the optimizer's own prompts, measured across "
        f"{registry.n_cycles_scanned} self-optimizing campaign(s) and ranked by how much "
        "each beat the unedited original on the same seeds",
        "",
        f"{'effect':>9}  {'95% CI':>19}  {'cells':>5}  {'obs':>4}  state",
    ]
    for c in registry.candidates[: args.top]:
        ci = f"[{c.ci_lo:+.4f}, {c.ci_hi:+.4f}]"
        # An interval straddling zero is the ordinary outcome on a 6-cell panel; say so per
        # row rather than letting the ranking imply every row above the fold is a winner.
        mark = " " if c.ci_lo <= 0.0 <= c.ci_hi else "*"
        lines.append(
            f"{c.anchor_effect:>+9.4f}  {ci:>19}  {c.n_cells:>5}  "
            f"{c.n_measurements:>4}{mark} {c.label}"
        )
    lines += [
        "",
        "* the interval excludes zero. Everything else is consistent with no effect — the "
        "ranking orders them, it does not endorse them.",
        "Graduating one is a hand-edit of promptpotter/assets/optimizer/pipeline.yaml.",
        *_snr_lines(registry.snr),
    ]
    return CommandResult(data=registry.model_dump(), human="\n".join(lines))


__all__ = ["cmd_rank_optimizer_prompts"]
