"""``cmd_matrix`` — resource-matrix verbs.

``measure`` scores a dataset's ORIGIN under one or more target-model overrides and
upserts the (target-model, dataset) verdicts into the pp-self
``resource_matrix.json``. Real backend spend (origin-only, cheap); the operator runs
it deliberately to build the L4 panel's capability grid.
"""

from __future__ import annotations

import argparse

from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    identity_from_args,
    init_services_cli,
)

__all__ = ["cmd_matrix"]


async def cmd_matrix(args: argparse.Namespace) -> CommandResult:
    """Dispatch ``matrix <verb>`` — currently ``measure``."""
    verb = args.matrix_verb
    if verb == "measure":
        return await _cmd_matrix_measure(args)
    raise SystemExit(f"matrix: unknown verb {verb!r}")


async def _cmd_matrix_measure(args: argparse.Namespace) -> CommandResult:
    """Score origin for each (dataset, model) cell; upsert into resource_matrix.json."""
    from promptpotter.application.resource_matrix.matrix import upsert_cells, write_matrix
    from promptpotter.application.resource_matrix.measure import measure_cells
    from promptpotter.infrastructure.store.dataset_access import readable_dataset_dir

    identity = identity_from_args(args)
    session = await init_services_cli(dataset_name=args.dataset, identity=identity)
    cells = await measure_cells(
        session, args.dataset, args.models, args.provider, int(args.samples)
    )

    # The artifact belongs to the pp-self panel, not the measured dataset.
    pp_self_dir = readable_dataset_dir(session.store, "promptpotter-self")
    matrix = upsert_cells(pp_self_dir, cells)
    path = write_matrix(pp_self_dir, matrix)

    header = (
        f"{'band':<10} {'origin':>7} {'constant':>9} {'top-ans':>8}  {'95% CI':>16}  "
        f"{'n':>4}  model  ({args.dataset})"
    )
    lines = [
        f"Resource matrix — measured {len(cells)} cell(s) on {args.dataset}; "
        f"matrix now {len(matrix.cells)} cell(s). Artifact: {path}",
        header,
        "-" * len(header),
    ]
    for c in cells:
        acc = "—" if c.origin_accuracy is None else f"{c.origin_accuracy:.3f}"
        ci = f"[{c.wilson_lo:.3f}, {c.wilson_hi:.3f}]"
        note = f"  ({c.note})" if c.note else ""
        lines.append(
            f"{c.band:<10} {acc:>7} {c.constant_floor:>9.3f} {c.predicted_share:>8.3f}  "
            f"{ci:>16}  {c.n:>4}  {c.target_model}{note}"
        )
    lines.append(
        "'constant' = what a single-label stub scores here; 'top-ans' = the share of answers "
        "the pipeline gave to its commonest label. An origin that fails to clear the constant, "
        "or that is far more single-minded than the truth, is 'floor' — no signal to optimize, "
        "whatever the absolute number."
    )
    return CommandResult(data=matrix.model_dump(), human="\n".join(lines))
