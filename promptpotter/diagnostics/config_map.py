"""Config map — the statistician's single view of *what overwrites what* and
*what clashes with what* across the optimization knobs.

The optimization knobs are not independent: several co-determine the same
statistical estimand (the scored subset, the difficulty ruler δ, the ability θ,
the gate), so a knob flipped in isolation can quietly make another ill-defined.
This diagnostic prints, from the one declared registry (``application.config_coupling``):

- every knob grouped by the **estimand** it moves, with its effective value and
  the **layer that value came from** (operator-set vs default vs hardcoded
  constant) — the provenance view;
- every declared **coupling** between knobs, flagged when the current config sits
  in its violating combination — the collision view.

Run it::

    python -m promptpotter.diagnostics.config_map                 # static registry (defaults)
    python -m promptpotter.diagnostics.config_map justlogic       # a dataset's campaign.json
    python -m promptpotter.diagnostics.config_map path/to/campaign.json

The same registry drives the pre-run preflight warning and the webapp config-map
panel, so this CLI, the terminal preflight, and the dashboard never disagree on
which knobs collide.
"""

from __future__ import annotations

import sys
from pathlib import Path

from promptpotter.application.config import CampaignConfig, OptimizationConfig
from promptpotter.application.config_coupling import (
    COUPLINGS,
    Estimand,
    KnobState,
    check_couplings,
    estimand_doc,
    knob_label,
    resolve_knob_states,
)
from promptpotter.application.datasets.authored import read_campaign_config_file


def _load_config(arg: str) -> CampaignConfig:
    """Load a ``CampaignConfig`` from a dataset name or a ``campaign.json`` path."""
    path = Path(arg)
    if not path.exists():
        path = Path("datasets") / arg / "campaign.json"
    if not path.exists():
        raise SystemExit(f"config_map: no campaign.json for {arg!r} (tried {path})")
    return CampaignConfig.model_validate(read_campaign_config_file(path))


def _default_config() -> CampaignConfig:
    """A defaults-only config for the static view — the two required thresholds
    are filled with placeholder values flagged ``required`` in the output."""
    return CampaignConfig(
        optimization=OptimizationConfig(improvement_threshold=0.0, degradation_threshold=0.0)
    )


_short = knob_label


def format_map(states: list[KnobState], violated_names: set[str], *, static: bool) -> str:
    by_estimand: dict[Estimand, list[KnobState]] = {e: [] for e in Estimand}
    for s in states:
        for e in s.estimands:
            by_estimand[e].append(s)
    lines = ["config map", ""]
    if static:
        lines.append(
            "(static registry — values are DEFAULTS; pass a dataset to resolve effective values)"
        )
        lines.append("")
    for e in Estimand:
        rows = by_estimand[e]
        if not rows:
            continue
        lines.append(f"# {e.value.upper()} -- {estimand_doc(e)}")
        width = max(len(_short(s.path)) for s in rows)
        for s in rows:
            tag = "" if s.source == "default" else f"  [{s.source}]"
            lines.append(f"    {_short(s.path).ljust(width)}  = {s.value!r}{tag}")
        lines.append("")
    lines.append("couplings -- what clashes with what ([X] = active in this config)")
    lines.append("")
    for c in COUPLINGS:
        mark = "[X]" if c.name in violated_names else "[ ]"
        knobs = ", ".join(_short(k) for k in c.knobs)
        lines.append(f"  {mark} [{c.severity}] {c.name}")
        lines.append(f"      knobs: {knobs}")
        lines.append(f"      {c.relation}")
        if c.name in violated_names:
            lines.append(f"      -> {c.consequence}")
        lines.append("")
    active = [c for c in COUPLINGS if c.name in violated_names]
    if active:
        lines.append(f"{len(active)} coupling(s) ACTIVE in this config:")
        for c in active:
            lines.append(f"  [X] [{c.severity}] {c.name} ({', '.join(_short(k) for k in c.knobs)})")
    else:
        lines.append("no couplings active in this config.")
    return "\n".join(lines)


def main(argv: list[str]) -> None:
    # The estimand docs + coupling text carry δ/θ; force UTF-8 so a cp1252 Windows
    # console renders them instead of raising UnicodeEncodeError.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    static = len(argv) == 0
    config = _default_config() if static else _load_config(argv[0])
    states = resolve_knob_states(config)
    violated = {c.name for c in check_couplings(config)}
    print(format_map(states, violated, static=static))


if __name__ == "__main__":
    main(sys.argv[1:])
