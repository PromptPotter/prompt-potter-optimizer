from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.domain.optimizer_state import CritiqueReadout
    from promptpotter.domain.pipeline_schema import PipelineSchema

shapes_optimizer_prompt(__name__)


def fmt_pct(x: float | None, spec: str = "{:.1%}") -> str:
    return "—" if x is None else spec.format(x)


def critique_axes(schema: PipelineSchema, *, offers_shots: bool) -> frozenset[str]:
    out: set[str] = set(schema.open_prompt_fields()) | ({"shot_ids"} if offers_shots else set())
    for node in schema.nodes:
        if node.name:
            out.add(node.name)
        for pk in node.param_keys:
            out.add(pk)
            if node.name:
                out.add(f"{node.name}.{pk}")
    return frozenset(out)


def _priority_fix_axis(priority_fix: str) -> str:
    head, sep, _ = priority_fix.partition(":")
    axis = head.strip()
    return axis if sep and axis.isidentifier() else ""


def format_l1_critique_for_prompt(
    critique: CritiqueReadout | None, axes: frozenset[str] | None = None
) -> str:
    if not critique:
        return ""
    parts: list[str] = []
    pf = critique.get("priority_fix") or ""
    if pf:
        parts.append(f"Fix: {pf}")
    sa = list(critique.get("suggested_axes") or [])
    # The steer's own axis leads its menu, so `Fix:` and `Axes:` never name different things.
    if lead := _priority_fix_axis(pf):
        sa = [lead, *(a for a in sa if a != lead)]
    if axes is not None:
        sa = [a for a in sa if a in axes]
    if sa:
        parts.append(f"Axes: {', '.join(sa)}")
    if fh := critique.get("failure_highlights"):
        parts.append("Failures:")
        for h in fh[:3]:
            parts.append(f"  {h}")
    if not parts:
        return ""
    # `critique` is in the citation enum, and a variant grounding on it cites this heading.
    return "\n".join(["CRITIQUE (last round's failures, distilled):", *parts])


__all__ = ["critique_axes", "fmt_pct", "format_l1_critique_for_prompt"]
