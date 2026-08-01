"""L1 behaviour checks — programmatic conformance for one round of L1 output.

Each check is a pure ``(round_dict, ctx) -> CheckResult`` function with no
I/O. ``round_dict`` is one ``.runtime/cache/rounds/round_NNNN.json`` payload as
written by ``AuditTrailView.flush``; ``ctx`` carries the per-round
context the check needs (prior rounds, OSP at round-start, the three
``context_object`` items).

Adding a new check is one function plus one entry in ``CHECK_REGISTRY``.
The registry is the single source of truth, so every surface that enumerates the
checks (``review.md``, the round-1 verdict) reads the same set.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from promptpotter.application.optimization.dispatch.injections.registry import (
    INJECTIONS,
    STALL_EXPLORATION,
    citable_fields,
)
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.l1_layout import coerce_l1_layout
from promptpotter.domain.opt_search_point import variant_prose_written
from promptpotter.domain.search_point import PARAM_SCOPE_KEYS

# Rounds before this one keep param-scope locked so prompt-field exploration
# settles first (see ``_check_param_scope_discipline``). Fixed policy, not a
# per-round knob — derived signals (stall depth, mutation history) drive the
# rest of the loop, but this floor stays constant.
PARAM_UNLOCK_ROUND = 3

__all__ = [
    "CHECK_REGISTRY",
    "CheckResult",
    "ValidatorContext",
    "extract_l1_variants",
    "run_all_checks",
]


def extract_l1_variants(container: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Walk ``nodes.l1_generate.output.response.variants`` on a round/audit dict.

    The same shape rides on both the round-summary dict (as written into
    ``index.json::rounds[N]``) and the per-round audit dict
    (``round_NNNN.json``); both are read by this codepath. Empty list when L1
    didn't fire or the response is malformed.
    """
    if not container:
        return []
    nodes = container.get("nodes") or {}
    node = nodes.get("l1_generate") or {}
    response = ((node.get("output") or {}).get("response")) or {}
    if isinstance(response, dict):
        return [v for v in (response.get("variants") or []) if isinstance(v, dict)]
    return []


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    passed: bool
    evidence: str


@dataclass(frozen=True)
class ValidatorContext:
    """Context shared by all behaviour checks for one round.

    ``prior_rounds`` are the round dicts strictly before ``round_num``,
    in order; ``opt_sp`` is the OSP snapshot at round-start
    (carries ``task_context`` + the live prompt fields); ``context_object``
    is the three task-decomposition strings L1's prompt was shown.
    ``exploration_budget`` is the round's escalation-panel exploration
    budget (``tight`` / ``normal`` / ``wide``) — read by
    ``evidence_grounding_present`` to gate the ``stall_exploration``
    escape hatch. ``None`` means the wiring layer could not determine it,
    in which case ``stall_exploration`` citations fail-open (no flag).
    """

    round_num: int
    prior_rounds: list[dict[str, Any]] = field(default_factory=list)
    opt_sp: dict[str, Any] = field(default_factory=dict)
    context_object: list[str] = field(default_factory=list)
    exploration_budget: str | None = None
    # Axes the round-start AxisIndex flagged as ``peaked``. Used by
    # ``evidence_grounding_present`` to reject variants that cite
    # ``axis_memory`` to justify mutating a peaked axis without naming a
    # rebut (the critique naming that axis, or exploration_budget=wide).
    # Populated by ``output.py::_compute_behavior_per_round`` from each round's
    # ``axis_memory_peaked`` field, stashed by ``persist_round``.
    peaked_axes: frozenset[str] = field(default_factory=frozenset)


CheckFn = Callable[[dict[str, Any], ValidatorContext], CheckResult]


# --- helpers ---------------------------------------------------------------

# Phrase-seed tokens: alphabetic-start, 3+ chars. Excludes digits and
# underscore-first tokens because it seeds noun-phrase substring matches, not set
# overlap.
_PHRASE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z_\-]{2,}")


def _variant_text_blob(variant: dict[str, Any]) -> str:
    """All free-form variant text checks may scan against.

    The prose reaches ``variant_prose_written`` so BOTH carriers count. Reading
    ``prompt_fields_override`` alone left an L4 variant's blob as its
    ``changes_description`` and nothing else — a one-sentence summary against which one
    measured "pass" was the substring ``prompt`` inside a Chinese sentence.
    """
    parts = [str(variant.get("changes_description") or "")]
    parts.extend(variant_prose_written(variant).values())
    for value in (variant.get("task_context_override") or {}).values():
        parts.append(str(value or ""))
    return "\n".join(parts).lower()


def _key_phrases(text: str, *, min_len: int = 4, max_phrases: int = 6) -> list[str]:
    """Extract candidate noun phrases from a brief — substring-match seeds."""
    seen: list[str] = []
    for token in _PHRASE_TOKEN_RE.findall(text or ""):
        lowered = token.lower()
        if len(lowered) < min_len or lowered in seen:
            continue
        seen.append(lowered)
        if len(seen) >= max_phrases:
            break
    return seen


# --- checks ----------------------------------------------------------------


def _check_context_object_honored(round_dict: dict[str, Any], ctx: ValidatorContext) -> CheckResult:
    """Each variant must reference at least one ``context_object`` item."""
    items = [c for c in ctx.context_object if isinstance(c, str) and c.strip()]
    variants = extract_l1_variants(round_dict)
    if not items:
        return CheckResult("context_object_honored", True, "no context_object items to honour")
    if not variants:
        return CheckResult("context_object_honored", True, "no variants emitted")

    item_seeds = [_key_phrases(item) for item in items]
    misses: list[str] = []
    for i, v in enumerate(variants):
        blob = _variant_text_blob(v)
        if not any(any(seed in blob for seed in seeds) for seeds in item_seeds if seeds):
            misses.append(f"C{i + 1}")
    if misses:
        return CheckResult(
            "context_object_honored",
            False,
            f"{len(misses)}/{len(variants)} variants ignored context_object: {misses[:3]}",
        )
    return CheckResult(
        "context_object_honored",
        True,
        f"{len(variants)}/{len(variants)} variants reference ≥1 context_object item",
    )


def _check_param_scope_discipline(round_dict: dict[str, Any], ctx: ValidatorContext) -> CheckResult:
    """No param-scope mutations while prompt-field exploration hasn't settled."""
    variants = extract_l1_variants(round_dict)
    if not variants:
        return CheckResult("param_scope_discipline", True, "no variants emitted")

    early = ctx.round_num < PARAM_UNLOCK_ROUND
    stale_field = _stale_prompt_field(ctx)
    if not early and stale_field is None:
        return CheckResult(
            "param_scope_discipline",
            True,
            f"unlocked: round ≥ {PARAM_UNLOCK_ROUND} and no stale prompt field",
        )

    offenders: list[str] = []
    for i, v in enumerate(variants):
        pp = v.get("pipeline_params_override") or {}
        if _touches_param_scope(pp):
            offenders.append(f"C{i + 1}")
    if offenders:
        reason = (
            f"round < {PARAM_UNLOCK_ROUND}"
            if early
            else f"prompt field {stale_field!r} unchanged for ≥2 rounds"
        )
        return CheckResult(
            "param_scope_discipline",
            False,
            f"{len(offenders)} variant(s) touched param scope ({reason}): {offenders[:3]}",
        )
    return CheckResult(
        "param_scope_discipline",
        True,
        "no variant touched temperature/max_tokens/reasoning_effort",
    )


def _check_evidence_grounding_present(
    round_dict: dict[str, Any], ctx: ValidatorContext
) -> CheckResult:
    """Each L1 variant must cite a panel field that justifies its mutation.

    Per ``promptpotter/CLAUDE.md`` L1 contract: *no data justifying a choice
    ⇒ do not gamble*. Fails when:

    - ``evidence_grounding`` is missing / not a dict, or
    - ``field`` names a panel this round's prompt did not render (including
      ``stall_exploration`` under a ``tight`` budget), or
    - ``citation`` is empty / whitespace, or
    - ``citation`` quotes text that is **nowhere in the prompt L1 was shown**.

    The citable set is re-derived from the round-start layout — the same
    :func:`citable_fields` call that built the prompt's menu. Membership in a
    hand-maintained list was checkable while the panel was absent from the prompt, which
    is the shape of a fabricated citation the check exists to catch.

    That derivation checks the NAME; the containment test checks the TEXT, and until it
    existed a variant could name a rendered panel and quote something invented — 3 of one
    live round's 6 citations were fabricated or misattributed and all six passed. The
    prompt is on disk in this same round file (``nodes.l1_generate.input.template_fields``,
    filled), so the evidence needs no new context."""
    variants = extract_l1_variants(round_dict)
    if not variants:
        return CheckResult("evidence_grounding_present", True, "no variants emitted")

    citable = _round_citable_fields(ctx)
    shown = _rendered_l1_prompt(round_dict)
    offenders: list[tuple[str, str]] = []
    for i, v in enumerate(variants):
        label = f"C{i + 1}"
        eg = v.get("evidence_grounding")
        if not isinstance(eg, dict):
            offenders.append((label, "missing"))
            continue
        field_name = str(eg.get("field") or "").strip()
        citation = str(eg.get("citation") or "").strip()
        if field_name not in citable:
            offenders.append((label, _uncitable_reason(field_name, ctx)))
            continue
        if not citation:
            offenders.append((label, "empty_citation"))
            continue
        if not _citation_in_prompt(citation, shown):
            offenders.append((label, "citation_not_in_prompt"))
            continue
        peaked_hit = _cited_peaked_axis(v, citation, field_name, ctx)
        if peaked_hit and not _has_peaked_rebut(v, citation, ctx):
            offenders.append((label, f"axis_memory_peaked={peaked_hit}"))
            continue

    if offenders:
        sample = ", ".join(f"{label}({reason})" for label, reason in offenders[:3])
        return CheckResult(
            "evidence_grounding_present",
            False,
            f"{len(offenders)}/{len(variants)} variants lack valid evidence: {sample}",
        )
    return CheckResult(
        "evidence_grounding_present",
        True,
        f"{len(variants)}/{len(variants)} variants cite a panel field",
    )


def _check_not_only_param_variants(
    round_dict: dict[str, Any], ctx: ValidatorContext
) -> CheckResult:
    """≥1 variant per round must mutate a prompt-field axis, WHEREVER it rides.

    Asking for ``prompt_fields_override`` specifically read the carrier, not the axis:
    on a cycle whose prose levers are node params (L4) that slot is structurally absent,
    so the check failed every round in which the generator did exactly what it was told.
    """
    variants = extract_l1_variants(round_dict)
    if not variants:
        return CheckResult("not_only_param_variants", True, "no variants emitted")

    for v in variants:
        if variant_prose_written(v):
            return CheckResult(
                "not_only_param_variants",
                True,
                "≥1 variant mutates a prompt-field axis",
            )
    return CheckResult(
        "not_only_param_variants",
        False,
        f"all {len(variants)} variants mutate only non-prompt-field params",
    )


def _check_changes_description_english(
    round_dict: dict[str, Any], ctx: ValidatorContext
) -> CheckResult:
    """``changes_description`` must stay in the language its optimizer prompt is written in.

    It is the loop's ONLY record of what a candidate intended — every downstream reader
    (``review.md``, the ALREADY TRIED panel, an operator triaging a round) is a reader of
    this string. A live round returned it entirely in Chinese: the payload was still valid
    English, every gate passed, and only the audit trail went dark. Majority non-ASCII
    letters, so a quoted foreign term costs nothing and a wholesale flip is caught. A
    behaviour check, not a rejection — an unreadable note is a bad record, not a bad edit.
    """
    variants = extract_l1_variants(round_dict)
    if not variants:
        return CheckResult("changes_description_english", True, "no variants emitted")

    offenders: list[str] = []
    for i, v in enumerate(variants):
        letters = [c for c in str(v.get("changes_description") or "") if c.isalpha()]
        if letters and sum(1 for c in letters if not c.isascii()) * 2 > len(letters):
            offenders.append(f"C{i + 1}")
    if offenders:
        return CheckResult(
            "changes_description_english",
            False,
            f"{len(offenders)}/{len(variants)} descriptions are majority non-ASCII: "
            f"{offenders[:3]}",
        )
    return CheckResult(
        "changes_description_english",
        True,
        f"{len(variants)}/{len(variants)} descriptions readable",
    )


# --- registry --------------------------------------------------------------

CHECK_REGISTRY: dict[str, CheckFn] = {
    "context_object_honored": _check_context_object_honored,
    "param_scope_discipline": _check_param_scope_discipline,
    "not_only_param_variants": _check_not_only_param_variants,
    "evidence_grounding_present": _check_evidence_grounding_present,
    "changes_description_english": _check_changes_description_english,
}


def run_all_checks(round_dict: dict[str, Any], ctx: ValidatorContext) -> list[CheckResult]:
    """Run every registered check against one round, in registry order.

    **Empty list when L1 emitted NO variants** — there is nothing to score, and an absent
    yield must not count as a conformance PASS. Every check short-circuits to
    ``passed=True`` ("no variants emitted") on an empty variant list, so running them anyway
    scored 4/4 and turned the worst L1 outcome — an optimizer prompt that makes its own children
    unreadable (``RoundResult.l1_parse_failure``) — into a perfect ``behavior_pass_rate`` of
    1.0 and a ``healthy`` round-1 verdict, which is the gate that authorises another round of
    real spend. "Nothing to check" is not "nothing wrong".

    Mirrors :func:`run_all_l2_checks`, which has always gated on whether L2 actually fired.
    """
    if not extract_l1_variants(round_dict):
        return []
    return [fn(round_dict, ctx) for fn in CHECK_REGISTRY.values()]


# --- support helpers used by checks ---------------------------------------


def _round_citable_fields(ctx: ValidatorContext) -> tuple[str, ...]:
    """The panels this round's l1_generate prompt rendered — the same derivation that built
    its citation menu, replayed off the round-start OSP snapshot.

    Falls open to every citable panel when the snapshot carries no layout (an older round
    file, or a wiring layer that couldn't supply one): a missing snapshot must not fail
    every variant in the round."""
    memory = ctx.opt_sp.get("memory") or {}
    layout = coerce_l1_layout(memory.get("l1_layout") if isinstance(memory, dict) else None)
    if layout is None:
        return tuple(sorted([n for n, i in INJECTIONS.items() if i.citable] + [STALL_EXPLORATION]))
    return citable_fields(layout, exploration_budget=ctx.exploration_budget)


_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")

# A quoted run shorter than this cannot adjudicate fabrication either way: it matches
# something in a 16k-char prompt by coincidence, and flagging its absence would be noise.
# The check abstains rather than guessing — a false accusation here downgrades a round's
# verdict, and the round-1 verdict is what authorises another round of real spend.
_CITATION_MIN_RUN = 12


def _normalize_quote(text: str) -> str:
    """Lowercase alphanumerics joined by single spaces — the form a quote survives in.

    A citation re-types a panel line, so it drifts on the things that carry no meaning:
    line wrapping, smart quotes, an em-dash the model rendered as a hyphen. Normalizing
    both sides means only the WORDS have to match.
    """
    return _NORMALIZE_RE.sub(" ", text.lower()).strip()


def _rendered_l1_prompt(round_dict: dict[str, Any]) -> str:
    """The filled ``l1_generate`` prompt this round actually sent, normalized."""
    node = ((round_dict.get("nodes") or {}).get("l1_generate")) or {}
    fields = ((node.get("input") or {}).get("template_fields")) or {}
    return _normalize_quote(" ".join(str(v) for v in fields.values() if isinstance(v, str)))


def _citation_in_prompt(citation: str, shown: str) -> bool:
    """Did this quote come out of the prompt? Abstains when it cannot tell.

    An honest citation may ELIDE (``"…"``), so the test is the longest ellipsis-free run
    rather than the whole string — a fabricated citation has no matching run at any length.
    Abstains (True) when the prompt is unavailable, so an older round file or a
    generation-only peek never fails every variant it holds.
    """
    if not shown:
        return True
    longest = max((_normalize_quote(part) for part in re.split(r"[.]{3}|…", citation)), key=len)
    if len(longest) < _CITATION_MIN_RUN:
        return True
    return longest in shown


def _uncitable_reason(field_name: str, ctx: ValidatorContext) -> str:
    """Why this citation is not admissible — the three cases read very differently."""
    if not field_name:
        return "no_field"
    if field_name == STALL_EXPLORATION:
        return "stall_exploration_when_tight"
    injection = INJECTIONS.get(field_name)
    if injection is None:
        return f"bad_field={field_name!r}"
    if not injection.citable:
        return f"not_evidence={field_name!r}"
    # A real evidence panel — just not one L1 was shown this round. The fabricated citation
    # the old set-membership check waved through.
    return f"panel_not_in_prompt={field_name!r}"


_REBUT_SIGNALS: tuple[str, ...] = (
    "priority_fix",
    "suggested_axes",
    "critique names",
    "exploration_budget=wide",
    "exploration_budget = wide",
    "wide exploration",
)


def _cited_peaked_axis(
    variant: dict[str, Any], citation: str, field_name: str, ctx: ValidatorContext
) -> str | None:
    """Return the first peaked axis that this variant's evidence_grounding
    + override fields appear to mutate, or None.

    The match is intentionally permissive: we look for the peaked-axis
    name as a substring in the citation OR as a key inside
    ``pipeline_params_override`` (e.g. ``llm_only.temperature`` matches
    ``temperature`` keyed under ``llm_only``). The validator only fires
    when ``field_name == "axis_memory"`` — citations grounded in other
    panels (critique, task_context, etc.) are independent evidence
    and not rejected by this rule.
    """
    if not ctx.peaked_axes or field_name != "axis_memory":
        return None
    pp = variant.get("pipeline_params_override") or {}
    flat_pp_axes: set[str] = set()
    if isinstance(pp, dict):
        for node, params in pp.items():
            if isinstance(params, dict):
                for p in params:
                    flat_pp_axes.add(f"{node}.{p}")
                    flat_pp_axes.add(str(p))
    for axis in ctx.peaked_axes:
        if axis in citation or axis in flat_pp_axes:
            return axis
        _, _, leaf = axis.partition(".")
        if leaf and (leaf in citation or leaf in flat_pp_axes):
            return axis
    return None


def _has_peaked_rebut(variant: dict[str, Any], citation: str, ctx: ValidatorContext) -> bool:
    """True iff the variant's citation/changes_description cites a real
    rebut for the peaked-axis guard: the critique naming the axis
    (priority_fix / suggested_axes), or an explicit wide-exploration
    budget. Permissive substring match — the prompt instructs L1 to name
    the rebut verbatim.
    """
    if ctx.exploration_budget == "wide":
        return True
    blob = " ".join(
        [
            citation,
            str(variant.get("changes_description") or ""),
        ]
    ).lower()
    return any(sig in blob for sig in _REBUT_SIGNALS)


def _touches_param_scope(pp_override: dict[str, Any]) -> bool:
    if not isinstance(pp_override, dict):
        return False
    for key, value in pp_override.items():
        if key in PARAM_SCOPE_KEYS:
            return True
        if isinstance(value, dict) and _touches_param_scope(value):
            return True
    return False


def _stale_prompt_field(ctx: ValidatorContext) -> str | None:
    """Return a prompt-string field name unchanged for the past 2 rounds, or None.

    A field that appears in zero variants for the last two rounds is "stale"
    and triggers the param-scope lock.
    """
    if len(ctx.prior_rounds) < 2:
        return None
    recent_two = ctx.prior_rounds[-2:]
    mutated_fields: set[str] = set()
    for r in recent_two:
        for v in extract_l1_variants(r):
            # Leaf of `field` / `node.field` — same axis either carrier wrote it through.
            mutated_fields.update(k.rpartition(".")[2] for k in variant_prose_written(v))
    for field_name in PROMPT_STRING_FIELDS:
        if field_name not in mutated_fields:
            return field_name
    return None
