from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import CritiqueReadout


def fmt_pct(x: float | None, spec: str = "{:.1%}") -> str:
    """``—`` for a measurement that was never taken. Rendering absence as ``0.0%`` is the one
    reading an operator cannot recover from: it looks like a campaign whose origin scored nothing,
    which is the shape of a broken pipeline rather than of a cycle that never got there.

    Every rate a surface prints routes here, because ``accuracy`` is nullable at the source and an
    f-string's format spec is the one place the type checker cannot follow the value to."""
    return "—" if x is None else spec.format(x)


def _valid_axis_set(schema: PipelineSchema) -> set[str]:
    """Schema-legitimate axes (open prompt fields + node names + param keys) — used to filter L2's
    hallucinated `suggested_axes` (e.g. `prompt_size`) before they seed the next round. A prompt
    field the campaign held is not one: steering L1 at it spends a round on a slot it cannot write.
    """
    out: set[str] = set(schema.open_prompt_fields()) | {"few_shot_examples", "plan"}
    for node in schema.nodes:
        if node.name:
            out.add(node.name)
        for pk in node.param_keys:
            out.add(pk)
            if node.name:
                out.add(f"{node.name}.{pk}")
    return out


def _priority_fix_axis(priority_fix: str) -> str:
    """The axis a ``<axis>: <change>`` steer names, so the menu beside it cannot omit it."""
    head, sep, _ = priority_fix.partition(":")
    axis = head.strip()
    return axis if sep and axis.isidentifier() else ""


def format_l1_critique_for_prompt(
    critique: CritiqueReadout | None, pipeline_schema: PipelineSchema | None = None
) -> str:
    if not critique:
        return ""
    parts: list[str] = []
    pf = critique.get("priority_fix") or ""
    if pf:
        parts.append(f"Fix: {pf}")
    sa = list(critique.get("suggested_axes") or [])
    # The steer's own axis leads its menu: a `Fix:` naming an axis `Axes:` omitted told the
    # generator two different things about one round, and it was the steer that got followed.
    if lead := _priority_fix_axis(pf):
        sa = [lead, *(a for a in sa if a != lead)]
    if pipeline_schema is not None:
        valid = _valid_axis_set(pipeline_schema)
        sa = [a for a in sa if a in valid]
    if sa:
        parts.append(f"Axes: {', '.join(sa)}")
    if fh := critique.get("failure_highlights"):
        parts.append("Failures:")
        for h in fh[:3]:
            parts.append(f"  {h}")
    if not parts:
        return ""
    # Titled like every panel beside it. `critique` is offered in the citation enum, but the block
    # rendered untitled, so variants grounding on it named whichever heading rendered above.
    return "\n".join(["CRITIQUE (last round's failures, distilled):", *parts])


__all__ = ["fmt_pct", "format_l1_critique_for_prompt"]
