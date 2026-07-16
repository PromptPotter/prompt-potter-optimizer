"""``cmd_champion`` — champion-registry verbs.

``refresh`` reduces the on-disk pp-self corpus to one ranked table of candidate
meta-prompt states (pure disk read, zero LLM — the ``ab``-verb posture);
``promote``/``coronate`` elect a champion; ``apply`` graduates the reigning
champion into the distributable ``datasets/_optimizer``; ``replay`` prints the
current state.
"""

from __future__ import annotations

import argparse

from promptpotter.presentation.cli.commands._shared import CommandResult, identity_from_args

__all__ = ["cmd_champion"]


async def cmd_champion(args: argparse.Namespace) -> CommandResult:
    """Dispatch ``champion <verb>`` — ``refresh`` / ``promote`` / ``coronate`` / ``apply`` / ``replay``."""
    verb = args.champion_verb
    if verb == "refresh":
        return _cmd_champion_refresh(args)
    if verb == "promote":
        return _cmd_champion_promote(args)
    if verb == "coronate":
        return _cmd_champion_coronate(args)
    if verb == "apply":
        return _cmd_champion_apply(args)
    if verb == "replay":
        return _cmd_champion_replay(args)
    raise SystemExit(f"champion: unknown verb {verb!r}")


def _cmd_champion_apply(args: argparse.Namespace) -> CommandResult:
    """Graduate the reigning champion into the distributable ``datasets/_optimizer``."""
    from promptpotter.application.meta_champion.champion import apply_champion_to_optimizer

    outcome = apply_champion_to_optimizer(dry_run=args.dry_run)
    lines = [outcome.detail]
    for c in outcome.changes:
        lines.append(f"  ~ {c.node}.{c.field}: {len(c.before)}c -> {len(c.after)}c")
    for s in outcome.skipped:
        lines.append(f"  · skipped {s}")
    if outcome.applied:
        lines.append(
            f"Wrote {outcome.path}. Review the diff and commit deliberately "
            "(the next `new promptpotter-self` inner origin now starts from the champion)."
        )
    elif outcome.changes and args.dry_run:
        lines.append("Dry run — re-run without --dry-run to write.")
    return CommandResult(data=outcome.model_dump(), human="\n".join(lines))


def _cmd_champion_promote(args: argparse.Namespace) -> CommandResult:
    """Elect a state as the reigning champion (writes the pointer)."""
    from promptpotter.application.meta_champion.champion import promote_champion
    from promptpotter.infrastructure.store import build_stores

    stores = build_stores(identity_from_args(args))
    pointer, message = promote_champion(stores.base_dir, args.state_hash)
    return CommandResult(data=pointer.model_dump() if pointer else {}, human=message)


def _cmd_champion_coronate(args: argparse.Namespace) -> CommandResult:
    """Head-to-head a challenger vs the reigning champion; crown it if the CI clears."""
    from promptpotter.application.meta_champion.champion import coronate
    from promptpotter.infrastructure.store import build_stores

    stores = build_stores(identity_from_args(args))
    outcome = coronate(stores.base_dir, args.state_hash, promote_on_win=not args.dry_run)
    human = (
        f"Coronation: {outcome.challenger} vs champion "
        f"{outcome.champion or '(none)'} — {outcome.decision.upper()}.\n"
        f"  pooled effect {outcome.effect:+.4f} [{outcome.ci_lo:+.3f}, {outcome.ci_hi:+.3f}] "
        f"over {outcome.n_shared_cells} shared cell(s).\n  {outcome.detail}"
    )
    return CommandResult(data=outcome.model_dump(), human=human)


def _cmd_champion_replay(args: argparse.Namespace) -> CommandResult:
    """Print the reigning champion + persisted table from disk (zero recompute)."""
    from promptpotter.application.meta_champion.champion import read_champion_pointer
    from promptpotter.application.meta_champion.reducer import read_registry
    from promptpotter.infrastructure.store import build_stores

    stores = build_stores(identity_from_args(args))
    pointer = read_champion_pointer()
    registry = read_registry(stores.base_dir)
    lines = []
    if pointer is None:
        lines.append("No reigning champion yet (run `champion coronate <hash>` or `promote`).")
    else:
        lines.append(
            f"Reigning champion: {pointer.state_hash} — anchor {pointer.anchor_effect:+.4f} "
            f"over {pointer.n_cells} cell(s), since {pointer.since}.\n  {pointer.label[:70]}"
        )
    if registry is not None:
        lines.append(
            f"Registry: {len(registry.candidates)} state(s), generated {registry.generated_at}."
        )
    else:
        lines.append("Registry not built yet — run `champion refresh`.")
    return CommandResult(
        data={
            "champion": pointer.model_dump() if pointer else None,
            "n_states": len(registry.candidates) if registry else 0,
        },
        human="\n".join(lines),
    )


def _cmd_champion_refresh(args: argparse.Namespace) -> CommandResult:
    """Rebuild the champion registry from disk and print the ranked table."""
    from promptpotter.application.meta_champion.reducer import reduce_corpus, write_registry
    from promptpotter.infrastructure.store import build_stores

    stores = build_stores(identity_from_args(args))
    registry = reduce_corpus(stores)
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
