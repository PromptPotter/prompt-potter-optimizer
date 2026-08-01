"""``cmd_rank_optimizer_prompts`` — rank every meta-prompt state on disk by paired candidate-minus-origin effect.

Read-only and **zero spend**: :func:`rank_optimizer_prompts` re-derives the ranking from round files
already written, so it pools every cell every past L4 run paid for and answers "which edit
actually held up" retroactively. It was reachable only over HTTP (``GET /optimizer-prompt-ranking``),
which a CLI-only operator has no route to — the same shape as the reaper having had only
server-lifespan call sites.

It NAMES a winner and writes nothing. Graduating one into
``promptpotter/assets/optimizer/pipeline.yaml`` stays a deliberate hand-edit (the verb that
used to write that file is gone, and the manifest is operator-owned).
"""

from __future__ import annotations

import argparse
import logging

from promptpotter.application.optimizer_prompt_ranking import rank_optimizer_prompts
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    get_verbose,
    identity_from_args,
)

logger = logging.getLogger("promptpotter.presentation.cli")


async def cmd_rank_optimizer_prompts(args: argparse.Namespace) -> CommandResult:
    """Rank the meta-prompt corpus; print the top ``--top`` states with their CIs."""
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
    ]
    return CommandResult(data=registry.model_dump(), human="\n".join(lines))


__all__ = ["cmd_rank_optimizer_prompts"]
