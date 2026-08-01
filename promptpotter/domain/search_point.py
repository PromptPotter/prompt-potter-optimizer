"""SearchPoint hierarchy + ``TaskDecomposition``."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import TYPE_CHECKING, Any

from pydantic import ConfigDict, field_validator

from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.hashing import content_hash

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema


PARAM_FORBIDDEN_KEYS: frozenset[str] = frozenset({"model", "provider"})
"""Optimizer-forbidden ``pipeline_params[node]`` keys (operator-fixed via dataset overlay)."""


PARAM_SCOPE_KEYS: frozenset[str] = frozenset(
    {"temperature", "max_tokens", "reasoning_effort", "top_p"}
)
"""Per-node LLM-call tunable axes (non-prompt). Drives param-scope discipline + continuous_envelope."""


class SearchPoint(StrictModel):
    """Abstract — subclassed by frozen ``JobSearchPoint`` and mutable ``OptSearchPoint``."""

    def render(self) -> str:
        raise NotImplementedError


class JobSearchPoint(SearchPoint):
    """Frozen target-layer point: ``pipeline_params`` + optional ``prompt_fields``."""

    model_config = ConfigDict(frozen=True)

    pipeline_params: dict[str, Any] | None = None
    prompt_fields: dict[str, Any] | None = None

    @field_validator("pipeline_params")
    @classmethod
    def _params_nested_by_node(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        """Reject flat ``{param: value}`` — shape is ``{node: {param: value}, "steps": [...]}``."""
        if v is None:
            return v
        flat = sorted(k for k, val in v.items() if isinstance(val, (str, int, float)))
        if flat:
            raise ValueError(
                f"pipeline_params must be nested-by-node — keys {flat} carry "
                "scalar values. Expected {node: {param: value}}, not a flat map."
            )
        return v

    def render(self) -> str:
        """Read the cached rendered prompt out of ``pipeline_params``."""
        for node_config in (self.pipeline_params or {}).values():
            if isinstance(node_config, dict) and "prompt" in node_config:
                return str(node_config["prompt"])
        return ""

    @property
    def config_params(self) -> dict[str, Any] | None:
        """``pipeline_params`` minus the per-node rendered-prompt injection — the
        config-only resolved view (``{node: {model, provider, reasoning_effort,
        temperature, …}}`` + ``steps``). The inverse of ``to_job_search_point``'s
        prompt injection: the rendered prompt rides ``prompt_fields`` / ``render()``,
        so the served resolved config never duplicates it on disk. Sole writer of
        the strip — the observe view reads this verbatim, never re-merges."""
        pp = self.pipeline_params
        if pp is None:
            return None
        return {
            node: (
                {k: v for k, v in cfg.items() if k != "prompt"} if isinstance(cfg, dict) else cfg
            )
            for node, cfg in pp.items()
        }

    def sp_hash(self, pipeline_schema: PipelineSchema) -> str:
        """SearchPoint identity hash, over the schema-resolved node configs.

        The schema is REQUIRED because it selects the algorithm. It used to default to
        ``None`` and hash the raw ``pipeline_params`` dict instead — two different hashes
        behind one name, and this value persists as ``prompt_fields_id`` on every
        measurement batch. One searchpoint reached the archive under two identities
        depending on whether its caller happened to have a schema in scope, and
        cross-run grouping split with no error anywhere.

        Role split (the codebase has TWO hashes — don't conflate): ``sp_hash`` is the
        optimizer/prompt-side dedup over the node-config structure; ``content_hash``
        (below) is the **measurement-archive key**. "Will this re-score?" is answered by
        ``content_hash`` / ``node_configs``, not this one."""
        return pipeline_schema.sp_hash(self.pipeline_params or {})

    def content_hash(self, dataset: list[Any]) -> str:
        """Content-addressed hash for measurement deduplication — the ``archive/
        measurements/`` key (rendered prompt + dataset + ``pipeline_params``). Because
        ``pipeline_params`` here is the overlay-merged set (config.py folds the connector
        ``model``/config in), two points differing only by model hash DIFFERENTLY and do
        NOT share measurements. This is the hash that decides re-score vs cache-replay."""
        return content_hash(
            self.render(),
            dataset,
            self.pipeline_params,
        )


# ---------------------------------------------------------------------------
# TaskDecomposition — structured domain context for optimizer LLM calls
# ---------------------------------------------------------------------------

# The FRAMING half: these render into the `task_context` panel of every optimizer prompt and
# are never measured directly — no candidate carries them, so nothing scores them. They are
# operator-authored knowledge about the task and are FROZEN for the run (`merge` refuses them).
#
# They used to be L2's rewrite surface, and the measurement said that was a bad trade: across
# 143 `l2_context` fires on disk, prose was 100% of what L2 emitted, each rewrite shared only
# 0.16 mean token overlap with the text it replaced (85% replacement, not the "accumulative
# refinement" the field docs claim), and no accuracy effect was detectable. Worse, the render
# capped each field and head-clipped the rest: 244 of the 258 states `key_challenges` ever held
# were over that cap, so ~95% of the time the operator's tail was silently amputated — including,
# on `justlogic-d234`, the measured finding that anti-hedging instructions BACKFIRE, which L1
# then re-proposed every round.
#
# A round's findings still reach L1 — through `critique`, `axis_memory` and `mutation_memory`,
# which are derived from measurement rather than paraphrased from the previous prompt.
FRAMING_FIELDS: frozenset[str] = frozenset(
    {
        "domain",
        "pipeline_purpose",
        "data_characteristics",
        "optimization_goals",
        "key_challenges",
    }
)

# Per-field authoring budget, enforced ONCE at mint (`TaskDecomposition.check_budget`) and
# never at render. That is the whole point: a budget a renderer enforces is a budget the
# author never sees until their words are already gone, and the author here is a human who
# can simply edit the file. Sized off what real framing needs — the widest field authored
# across the shipped datasets is ~420 chars, and 600 leaves room to say something without
# inviting a page.
FRAMING_VALUE_BUDGET = 600


@dataclass
class TaskDecomposition:
    """Typed domain context produced by task-description decomposition.

    All fields default to ``""`` so the object is always safe to read
    without ``.get()`` guards. Operator/LLM-facing — these strings ride
    the ``task_context`` injection into every optimizer prompt (L1, L1
    critique, L2, L3), so the per-field semantics are load-bearing.

    Field semantics (also reflected in the L2 optimizer prompt):

    - ``domain`` — one-line task family ("competition mathematics",
      "biomedical entity normalization"). Steers L1's persona /
      thinking_style; rarely changes after origin.
    - ``pipeline_purpose`` — one-sentence "what this campaign is
      trying to produce", framed for an outside reader. Set at
      origin, refined by L2 only on plan-level shifts.
    - ``data_characteristics`` — concrete properties of the sample
      pool L1 should account for (length, modality, distribution
      skew, known bias). Evidence-anchored — cites observations from
      the critique / diagnostics panels, not speculation.
    - ``optimization_goals`` — what we're optimising for, in
      operator vocabulary (accuracy vs latency vs compactness). Maps
      conceptually to the composite-fitness formula but is not its
      verbatim text.
    - ``key_challenges`` — the failure patterns L1 should defend
      against next round. Operator-authored and frozen like its four
      framing peers (see :data:`FRAMING_FIELDS` and ``merge``); it was
      L2's refinement surface, and the measurement that closed that is
      recorded above. A round's own findings reach L1 through
      ``critique`` / ``axis_memory`` / ``mutation_memory`` instead.
    - ``upstream_context`` — task-framing prepended around
      ``problem_description`` at render time (see
      ``OptSearchPoint._field_value``).
    - ``downstream_context`` — task-framing appended around
      ``problem_description`` at render time.
    - ``raw_description`` — verbatim operator-supplied task
      description (``datasets/{name}/task_description.md``);
      preserved separately so refinements never overwrite the source.
    """

    domain: str = ""
    pipeline_purpose: str = ""
    data_characteristics: str = ""
    optimization_goals: str = ""
    key_challenges: str = ""
    upstream_context: str = ""
    downstream_context: str = ""
    raw_description: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> TaskDecomposition:
        """Construct from dict; coerces list values to comma-joined strings."""
        if not d:
            return cls()
        known = {f.name for f in fields(cls)}
        coerced: dict[str, str] = {}
        for k, v in d.items():
            if k not in known:
                continue
            if isinstance(v, list):
                coerced[k] = ", ".join(str(item) for item in v)
            elif v is None:
                coerced[k] = ""
            else:
                coerced[k] = str(v)
        return cls(**coerced)

    @classmethod
    def coerce(cls, v: TaskDecomposition | dict[str, Any] | None) -> TaskDecomposition:
        """Normalize a typed | dict | None value into a ``TaskDecomposition``.

        The single "already typed ⇒ passthrough, else build from dict/None" contract,
        shared by the OSP field validator, the runner seam, and the L2 verbatim check —
        so those callers can't drift on how a raw task_context is admitted.
        """
        if isinstance(v, TaskDecomposition):
            return v
        return cls.from_dict(v)

    def merge(self, overrides: dict[str, Any]) -> TaskDecomposition:
        """Apply ``overrides``, refusing any FRAMING field.

        The five framing fields are operator-authored and **frozen for the run** — the
        loop reads them, nothing rewrites them. Enforced HERE, in the type, because it is
        the one place every writer must pass through: a caller that means to re-frame the
        task fails loud instead of quietly replacing curated knowledge with a paraphrase.

        Only ``upstream_context`` / ``downstream_context`` (spliced into the TARGET prompt
        by ``OptSearchPoint._field_value``, so a candidate carrying them is measured) and
        ``raw_description`` remain mutable — that is L1's ``task_context_override`` surface.
        """
        if forbidden := sorted(FRAMING_FIELDS & overrides.keys()):
            raise ValueError(
                f"task_context framing is frozen for the run — refusing to overwrite "
                f"{forbidden}. These fields are operator-authored evidence about the task; "
                f"a round's findings belong in the critique / axis_memory / mutation_memory "
                f"channels, which are derived from measurement. Mutable here: "
                f"{sorted({f.name for f in fields(self)} - FRAMING_FIELDS)}."
            )
        base = self.to_dict()
        base.update(overrides)
        return self.from_dict(base)

    def check_budget(self, *, source: str) -> None:
        """Raise if any FRAMING field exceeds :data:`FRAMING_VALUE_BUDGET`.

        Called once, at the run-start seam, so an over-budget field stops the campaign
        before it starts instead of being clipped on every render for the campaign's whole
        life. ``source`` names the file to edit — the error is only useful if it says where.
        """
        over = [
            (k, len(v))
            for k, v in self.to_dict().items()
            if k in FRAMING_FIELDS and len(v) > FRAMING_VALUE_BUDGET
        ]
        if not over:
            return
        detail = ", ".join(f"{k} is {n} chars" for k, n in sorted(over))
        raise ValueError(
            f"task_context framing exceeds the {FRAMING_VALUE_BUDGET}-char per-field budget "
            f"in {source}: {detail}. These fields render verbatim into every optimizer "
            f"prompt — edit them down rather than letting a renderer choose which half the "
            f"model sees. Put the detail in task_description.md, which has no budget."
        )

    def items(self) -> list[tuple[str, str]]:
        return [(f.name, getattr(self, f.name)) for f in fields(self)]

    def __len__(self) -> int:
        return sum(1 for f in fields(self) if getattr(self, f.name))

    def __bool__(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))


__all__ = [
    "PARAM_FORBIDDEN_KEYS",
    "PARAM_SCOPE_KEYS",
    "JobSearchPoint",
    "SearchPoint",
    "TaskDecomposition",
]
