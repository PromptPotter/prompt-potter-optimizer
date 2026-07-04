"""``cmd_champion`` — champion-registry verbs.

``refresh`` reduces the on-disk pp-self corpus to one ranked table of candidate
meta-prompt states (pure disk read, zero LLM — the ``ab``-verb posture). The
coronation-match + ``replay`` verbs land with the L4 champion-selection slice.
"""

from __future__ import annotations

import argparse

from promptpotter.presentation.cli.commands._shared import CommandResult, identity_from_args

__all__ = ["cmd_champion"]


async def cmd_champion(args: argparse.Namespace) -> CommandResult:
    """Dispatch ``champion <verb>`` — currently ``refresh``."""
    verb = args.champion_verb
    if verb == "refresh":
        return _cmd_champion_refresh(args)
    raise SystemExit(f"champion: unknown verb {verb!r}")


def _cmd_champion_refresh(args: argparse.Namespace) -> CommandResult:
    """Rebuild the champion registry from disk and print the ranked table."""
    from promptpotter.application.meta_champion import reduce_corpus, write_registry
    from promptpotter.infrastructure.store import build_stores

    stores = build_stores(identity_from_args(args))
    registry = reduce_corpus(stores.base_dir)
    path = write_registry(stores.base_dir, registry)

    if not registry.candidates:
        human = (
            f"Champion refresh: scanned {registry.n_cycles_scanned} pp-self cycle(s) under "
            f"{stores.base_dir / 'campaigns'} — no candidate meta-prompt states on disk yet. "
            f"Registry written to {path}."
        )
        return CommandResult(data=registry.model_dump(), human=human)

    header = f"{'#':>2}  {'state':<12} {'anchor':>9}  {'95% CI':>18}  {'cells':>5} {'n':>4}  label"
    lines = [
        f"Champion table — {len(registry.candidates)} state(s) over "
        f"{registry.n_cycles_scanned} cycle(s), ranked by anchor-to-origin effect "
        f"(provisional; coronation confirms). Registry: {path}",
        header,
        "-" * len(header),
    ]
    for i, row in enumerate(registry.candidates, start=1):
        ci = f"[{row.ci_lo:+.3f}, {row.ci_hi:+.3f}]"
        lines.append(
            f"{i:>2}  {row.state_hash:<12} {row.anchor_effect:>+9.4f}  {ci:>18}  "
            f"{row.n_cells:>5} {row.n_measurements:>4}  {row.label[:56]}"
        )
    return CommandResult(data=registry.model_dump(), human="\n".join(lines))
