"""The export tiers: one serializer each, over facts the backend already serves, for every surface."""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, assert_never

from promptpotter.application.views.render.primitives import bench_readout, fmt_pct
from promptpotter.domain.connector import MeasuredUnit, unit_count
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.export import ExportTier, PromptExport
from promptpotter.domain.hierarchy import HierarchyCandidate, HierarchyRound, SubtreeCourse
from promptpotter.domain.paired_reading import READING_STATE_INFO, PairedReading
from promptpotter.domain.treatment import Treatment
from promptpotter.domain.verify_pass import VERIFY_STRATEGY_LABELS
from promptpotter.shared.errors import ConflictError


@dataclass(frozen=True)
class Provenance:
    """What a reader needs to find the run again: the header every tier carries."""

    tool_version: str
    campaign_id: str
    cycle_id: str
    dataset_name: str
    dataset_hash: str
    treatment: Treatment
    # By node, the model the selection runs on.
    models: dict[str, str]
    # The cycle's own ledger line its served state stands at; ``None`` before its wiring.
    ledger_offset: int | None

    @property
    def base(self) -> str:
        """An Agent Skills name: lowercase, digits and hyphens, at most 64."""
        slug = re.sub(r"[^a-z0-9]+", "-", f"{self.dataset_name}-{self.cycle_id}".lower())
        return slug.strip("-")[:64].rstrip("-")


@dataclass(frozen=True)
class ExportFiles:
    """One tier's files by path under ``name``, the folder or download it is saved as."""

    name: str
    files: dict[str, str]

    @property
    def single(self) -> str | None:
        return next(iter(self.files.values())) if len(self.files) == 1 else None

    @property
    def download_name(self) -> str:
        if len(self.files) == 1:
            return f"{self.name}{PurePosixPath(next(iter(self.files))).suffix}"
        return f"{self.name}.zip"

    def zipped(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
            for path, text in self.files.items():
                archive.writestr(f"{self.name}/{path}", text)
        return buf.getvalue()


def export_files(
    tier: ExportTier,
    course: SubtreeCourse,
    provenance: Provenance,
    *,
    unit: MeasuredUnit,
    export: PromptExport | None,
    no_export: str,
) -> ExportFiles:
    """*no_export* says why ``export`` is absent, for the tiers that cannot stand without it."""

    def needs_export() -> PromptExport:
        if export is None:
            raise ConflictError(f"The {tier} tier carries export.json, and {no_export}")
        return export

    base = provenance.base
    match tier:
        case "chat":
            return ExportFiles(f"{base}-chat", {"candidates.txt": chat_text(course, unit)})
        case "report":
            text = "\n".join([*provenance_lines(provenance, "report"), "", chat_text(course, unit)])
            return ExportFiles(f"{base}-report", {"report.txt": text})
        case "notebook":
            return ExportFiles(
                f"{base}-notebook",
                {
                    "candidates.jsonl": candidates_jsonl(course),
                    "README.md": dataset_card(provenance, "candidates.jsonl"),
                },
            )
        case "deploy":
            return ExportFiles(f"{base}-export", {"export.json": needs_export().to_json()})
        case "package":
            winner = needs_export()
            return ExportFiles(
                base,
                {
                    "SKILL.md": skill_md(winner, provenance),
                    **{f"prompts/{node}.md": winner.render(node) + "\n" for node in winner.prompts},
                    "export.json": winner.to_json(),
                    "evidence/candidates.jsonl": candidates_jsonl(course),
                },
            )
        case _:
            assert_never(tier)


def winner_pointer(hop: CycleHop) -> str:
    """The one statement of a cycle's winner: `log.md`'s `## Winner` and the notebook cell both say it."""
    return (
        "Stated once, by the package — the prompt per node, `export.json` and the evidence: "
        f"`python -m promptpotter export --campaign {hop.campaign_id} --cycle {hop.cycle_id} "
        f"--tier package`. Its machine record is `cycles/{hop.cycle_id}/export.json`."
    )


def provenance_lines(p: Provenance, kind: str) -> list[str]:
    """The first line is the stamp every copy opens with, so a paste back is recognised."""
    stamp = json.dumps({"tool": "promptpotter", "kind": kind, "tool_version": p.tool_version})
    models = ", ".join(f"{node}={model}" for node, model in p.models.items()) or "—"
    t = p.treatment
    return [
        stamp,
        f"campaign {p.campaign_id} · cycle {p.cycle_id}",
        f"dataset {p.dataset_name} · dataset_hash {p.dataset_hash}",
        f"optimizer {t.optimizer} {t.version} · manifest {t.manifest} · source {t.source}",
        f"model {models}",
        f"ledger offset {'—' if p.ledger_offset is None else p.ledger_offset}",
    ]


def _live(course: SubtreeCourse) -> list[HierarchyCandidate]:
    """The live side of each supersede cut: a retired tail doubles its round."""
    return [
        c
        for rnd in course.children
        if isinstance(rnd, HierarchyRound)
        for c in rnd.children
        if c.tag("superseded") is None
    ]


def chat_text(course: SubtreeCourse, unit: MeasuredUnit) -> str:
    """One block per candidate in round order, every number a served one under its served name."""
    arms = _live(course)
    label_of: dict[str, str] = {}
    for c in arms:
        label_of.setdefault(c.facts.individual, c.label)
    lines = [
        f"candidates of {course.label} · {len(arms)} rows · elected on "
        f"{course.facts.elects_on or '—'} · in round order"
    ]
    for c in arms:
        lines += ["", *_arm_lines(c, label_of, unit)]
    return "\n".join(lines) + "\n"


def _signed(v: float | None) -> str:
    return "—" if v is None else f"{v:+.3f}"


def _band(lo: float | None, hi: float | None, fmt: Callable[[float], str]) -> str:
    return "" if lo is None or hi is None else f" [{fmt(lo)}, {fmt(hi)}]"


def _arm_lines(c: HierarchyCandidate, label_of: dict[str, str], unit: MeasuredUnit) -> list[str]:
    r = c.facts.reading
    parents = [label_of.get(p, p) for p in c.facts.parent_ids]
    verdict = c.tag("verdict")
    head = [
        c.label,
        f"R{r.arm.round}",
        f"parent {' x '.join(parents)}" if parents else "origin",
        "—" if verdict is None else verdict.label,
    ]
    out = ["  ".join(head)]

    walk = " · ".join(w for w in (r.state, r.end) if w is not None)
    scored, expected = r.panel.scored, r.panel.expected
    cells = ""
    if scored is not None:
        held = f"{scored} of {unit_count(expected, unit)}" if r.panel.cut and expected else None
        cells = f" {held or unit_count(scored, unit)}"
    own = r.own
    accuracy = (
        "accuracy —"
        if own is None or own.accuracy is None
        else f"accuracy {fmt_pct(own.accuracy.value)}"
        f"{_band(own.accuracy.ci_lo, own.accuracy.ci_hi, fmt_pct)} n={own.n}"
    )
    composite = (
        "" if own is None or own.composite is None else f" · composite {own.composite.value:.2f}"
    )
    ability = r.ability
    theta = ""
    if ability is not None and ability.theta is not None:
        se = "" if ability.se is None else f" ± {ability.se:.2f}"
        caveat = f" ({ability.caveat})" if ability.caveat else ""
        theta = f" · θ {ability.theta:.2f}{se}{caveat}"
    out.append(f"  {walk}{cells} · {accuracy}{composite}{theta}")

    if r.vs_reference is not None:
        out.append(f"  {_pair_line(r.vs_reference, unit)}")

    loci = list(dict.fromkeys(locus for v in c.facts.variations for locus in v.loci))
    description = " ".join(r.changes_description.split())
    if loci or description:
        changed = f"changed {', '.join(loci)}" if loci else ""
        out.append(f"  {' — '.join(part for part in (changed, description) if part)}")

    extras: list[str] = []
    if (bench := r.bench) is not None:
        level = bench.level
        shown = (
            "—"
            if level is None
            else f"{fmt_pct(level.value)}{_band(level.ci_lo, level.ci_hi, fmt_pct)}"
        )
        extras.append(f"bench {shown} n={bench.n} held out")
    if (verify := r.verify) is not None:
        fresh = verify.fresh
        extras.append(
            f"verify {fmt_pct(None if fresh.accuracy is None else fresh.accuracy.value)} on "
            f"{unit_count(fresh.n, unit)} fresh, {VERIFY_STRATEGY_LABELS[verify.strategy]}"
        )
    if (line := r.on_origin_panel) is not None:
        extras.append(f"overlap {fmt_pct(line.rate)} on {unit_count(line.n, unit)} shared")
    if extras:
        out.append(f"  {' · '.join(extras)}")
    return out


def _pair_line(pair: PairedReading, unit: MeasuredUnit) -> str:
    member = pair.a
    against = "parent" if member is None or member.address.arm is None else member.address.arm.label
    lift, coverage = pair.headline, pair.coverage
    if lift is None or coverage is None:
        return f"vs {against}: {READING_STATE_INFO[pair.state].label}"

    def rate(v: float) -> str:
        return fmt_pct(v) if lift.measurand.unit == "rate" else f"{v:.2f}"

    estimate = lift.estimate
    # The flips ride the one measurand declared binary, whichever column the headline is.
    flips = next((m.flips for m in (lift, *pair.beside) if m.flips is not None), None)
    return " · ".join(
        [
            f"vs {against} {rate(lift.rate_a)} → {rate(lift.rate_b)}",
            f"lift {_signed(estimate.value)} [{_signed(estimate.ci_lo)}, "
            f"{_signed(estimate.ci_hi)}] {estimate.side}",
            *([f"flips +{flips.gained} -{flips.lost} ={flips.unchanged}"] if flips else []),
            f"on {unit_count(coverage.scored, unit)} both scored",
        ]
    )


def _flat(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            out |= _flat(item, f"{prefix}{key}.")
        else:
            out[f"{prefix}{key}"] = item
    return out


# A pair member's address path repeats the cycle every record sits in, which the card states once.
_CYCLE_ADDRESS: dict[str, Any] = {
    "vs_reference": {"a": {"address": {"path"}}, "b": {"address": {"path"}}}
}


def candidate_records(course: SubtreeCourse) -> list[dict[str, Any]]:
    """One flat record per candidate, its reading keyed by the names the API serves it under."""
    return [
        {
            "label": c.label,
            "id": c.facts.individual,
            "parent_ids": c.facts.parent_ids,
            "verdict": None if (verdict := c.tag("verdict")) is None else verdict.value,
            "stands": c.facts.stands,
            "loci": list(dict.fromkeys(locus for v in c.facts.variations for locus in v.loci)),
            "members": [v.node for v in c.facts.variations],
            **_flat(c.facts.reading.model_dump(mode="json", exclude=_CYCLE_ADDRESS)),
        }
        for c in _live(course)
    ]


def candidates_jsonl(course: SubtreeCourse) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in candidate_records(course))


def _yaml_str(text: str) -> str:
    """A JSON string is a YAML double-quoted scalar, so no YAML library is needed."""
    return json.dumps(text, ensure_ascii=False)


def dataset_card(p: Provenance, data_file: str) -> str:
    """A Hugging Face dataset card: YAML frontmatter, then what one record is."""
    return "\n".join(
        [
            "---",
            f"pretty_name: {_yaml_str(f'{p.dataset_name} candidates, {p.cycle_id}')}",
            "tags:",
            "- promptpotter",
            "- prompt-optimization",
            "configs:",
            "- config_name: default",
            f"  data_files: {data_file}",
            "---",
            "",
            f"# {p.dataset_name} candidates — {p.cycle_id}",
            "",
            f"`{data_file}` holds one record per candidate this cycle measured, in round order, "
            "all of the one campaign and cycle below, so a record carries arm keys and no cycle "
            "address. It is keyed by the names the PromptPotter API serves (`arm.label`, "
            "`own.accuracy.value`, "
            "`vs_reference.headline.estimate.value`, …). A level is read on the candidate's own "
            "round's cells, so two rows of different rounds are compared only on "
            "`on_origin_panel.rate`, the one set of cells they share. A null is a reading not "
            "taken, never a zero.",
            "",
            "```",
            *provenance_lines(p, "notebook"),
            "```",
            "",
        ]
    )


def _bench_readout(export: PromptExport) -> tuple[str, str]:
    reading = export.bench.vs_origin
    lift = reading.headline
    if lift is None:
        return READING_STATE_INFO[reading.state].label, "—"
    est = lift.estimate
    return _signed(est.value), f"[{_signed(est.ci_lo)}, {_signed(est.ci_hi)}]"


def skill_md(export: PromptExport, p: Provenance) -> str:
    """Agent Skills' envelope: ``name`` and ``description`` required, the binding in ``metadata``."""
    models = ", ".join(f"{node}={model}" for node, model in p.models.items()) or "—"
    lift, ci = _bench_readout(export)
    m = export.measurement
    description = (
        f"Run the prompt PromptPotter selected for {export.dataset_name} on {models}. "
        "Use it on that model only: an optimized prompt does not carry across models."
    )
    metadata = {
        "model": models,
        "dataset": export.dataset_name,
        "dataset_hash": export.dataset_hash,
        "scorer": export.bench.scorer_id,
        "bench_lift": lift,
        "bench_ci": ci,
        "campaign_id": export.campaign_id,
        "cycle_id": export.cycle_id,
        "tool_version": export.tool_version,
    }
    accuracy = m.own.accuracy
    level = (
        "—"
        if accuracy is None
        else f"{fmt_pct(accuracy.value)}{_band(accuracy.ci_lo, accuracy.ci_hi, fmt_pct)}"
    )
    run = [
        f"- `{node}`: send `prompts/{node}.md` as its prompt, on "
        f"`{export.tuned_params.get(node, {}).get('model', '—')}`; the rest of its params are "
        f"`export.json` → `tuned_params.{node}`."
        for node in export.prompts
    ]
    return "\n".join(
        [
            "---",
            f"name: {p.base}",
            f"description: {_yaml_str(description)}",
            "metadata:",
            *(f"  {key}: {_yaml_str(value)}" for key, value in metadata.items()),
            "---",
            "",
            f"# {export.dataset_name} — the selected prompt",
            "",
            "## Run it",
            "",
            *run,
            "",
            "## How good it is",
            "",
            f"- its own level, round {m.round}, under `{m.formula or '—'}`: accuracy {level} "
            f"n={m.own.n}, on the rows that chose it — not an estimate",
            f"- held out: {bench_readout(export.bench)}",
            "",
            "## Files",
            "",
            "- `export.json` — the machine record: the prompt by field name, its provenance, and "
            "`lineage`, every candidate as a delta over its parent (PROV-O keys, JSON-LD "
            "`@context`).",
            "- `evidence/candidates.jsonl` — every candidate's served reading, one record each, all "
            "of this `campaign_id` and `cycle_id` (arm keys only, no address).",
            "",
        ]
    )
