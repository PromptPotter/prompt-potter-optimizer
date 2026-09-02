"""The closed outbound SSE set declared in ``docs/specs/m12-events-asyncapi.yaml``. ``sequence`` is the ledger
offset — a subscriber detects gaps by it and replays from the family ray, never via a ``since=`` on the tail."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, get_args

from pydantic import ConfigDict, Field

from promptpotter.domain.run_records import CycleRecord
from promptpotter.domain.strict_model import StrictModel

__all__ = [
    "NON_ACTIVITY_KINDS",
    "RAY_PAYLOAD_FIELDS",
    "RENDERS_AS_ACTIVITY",
    "ProjectionEnvelope",
    "ProjectionKind",
    "ray_payload",
]


# Closed enum mirroring ``ProjectionEnvelope.kind`` in ``docs/specs/m12-events-asyncapi.yaml``.
#
# **A missing kind is a HOLE, not a filter.** ``CycleLedgerTail.read_new`` advances
# ``_line_index`` for every line it reads, including one whose kind it cannot map — so an
# unlisted record is skipped while still consuming an offset, and the client sees a ``sequence``
# gap and fires the reconnect recipe in ``docs/developer/event-stream.md``.
ProjectionKind = Literal[
    # `record_type` literals — the complete `CycleRecord` union
    "candidate_minted",
    "decision",
    "command",
    "command_ack",
    "cycle_seed",
    "election",
    "error",
    "llm_call_progress",
    "llm_call",
    "llm_call_start",
    "phase",
    "round_warning",
    "ruler",
    "snapshot",
    "spend_tombstone",
    "token_usage",
    # projection-only — synthesized by the ledger tail (``CycleLedgerTail``)
    "stream_snapshot",
]

# The coverage rule as an import-time raise, both directions: a `CycleRecord` arm with no kind is
# the silent offset-burning hole above, and a kind naming no arm is a wire promise nothing sends.
_PROJECTION_ONLY = frozenset({"stream_snapshot"})
_arms = {arm.model_fields["record_type"].default: arm for arm in get_args(get_args(CycleRecord)[0])}
_record_types = frozenset(_arms)
_declared = frozenset(get_args(ProjectionKind)) - _PROJECTION_ONLY
if _declared != _record_types:
    raise RuntimeError(
        "ProjectionKind must cover the CycleRecord union exactly — "
        f"missing {sorted(_record_types - _declared)}, "
        f"unbacked {sorted(_declared - _record_types)}."
    )

# Whether a kind can EVER become an item in the activity feed — the ONE declaration of the
# feed's vocabulary, which is why the ray needs none of its own. ``False`` is the licence not to
# serve the record at all (``store/family_ray_views.py``); ``True`` still renders conditionally
# on the payload — a ``snapshot`` that is a ``p_best_update`` yields nothing — and THAT decision
# belongs to the renderer. Total over the RECORD kinds: ``stream_snapshot`` is synthesized by
# the tail, reaches its own translator, and is on no ledger for the ray to filter.
RENDERS_AS_ACTIVITY: dict[ProjectionKind, bool] = {
    "candidate_minted": True,
    "command": True,
    "command_ack": True,
    "cycle_seed": True,
    "decision": False,
    "election": False,
    "error": True,
    "llm_call": True,
    "llm_call_progress": True,
    "llm_call_start": True,
    "phase": True,
    "round_warning": True,
    "ruler": False,
    "snapshot": True,
    "spend_tombstone": False,
    "token_usage": False,
}

if frozenset(RENDERS_AS_ACTIVITY) != _declared:
    raise RuntimeError(
        "RENDERS_AS_ACTIVITY must answer for every record kind — an unanswered one is a record "
        "the ray cannot decide about and the feed drops in silence: "
        f"missing {sorted(_declared - frozenset(RENDERS_AS_ACTIVITY))}, "
        f"unbacked {sorted(frozenset(RENDERS_AS_ACTIVITY) - _declared)}."
    )

#: The complement, as both consumers want it: the ray's drop set and the client's proof that
#: nothing reaching its default arm was ever meant to render.
NON_ACTIVITY_KINDS: frozenset[ProjectionKind] = frozenset(
    kind for kind, renders in RENDERS_AS_ACTIVITY.items() if not renders
)


# WHAT of a bearing record rides the ray, per kind — the field half of the question
# ``RENDERS_AS_ACTIVITY`` answers by kind, which is why it is declared beside it.
#
# The ray serves a WINDOW (up to ``MAX_RAY_LIMIT`` records at once), so a record's whole
# ``model_dump`` is the wrong unit: the chronology answers *what happened, in order*, and a
# record's bulk — an LLM's prompt and response, a sample's query and prediction, a phase's
# whole view, an origin's prompt fields — is already addressable at the surface built for it
# (the audit twin, the round document, ``dashboard.json``), each of which is fetched ONE
# round at a time. Measured over the banked ledgers, that bulk is ~95% of the served bytes.
#
# So a kind names IDENTITY, ADDRESS, and the ONE-LINE READING, and nothing else. **Decided
# here rather than read off the client**: a server filter whose correctness is defined by a
# TypeScript file is the seam defect, not the fix — the renderer still decides how a kind
# LOOKS, and a field it stops using is deleted here deliberately, never by drifting apart.
#
# A dotted path reaches into the untyped ``payload`` dict; only its first segment can be
# checked against the model, which is exactly the rename that has silently emptied a panel
# before. ``record_type`` and ``timestamp`` ride nowhere — ``RayItem`` carries both as its
# own fields, so repeating them inside the body is one line of every record wasted.
RAY_PAYLOAD_FIELDS: dict[ProjectionKind, frozenset[str]] = {
    # The attempt as it was proposed. `changes_description` is the prose the round document
    # and the tree both carry, and it is the largest thing on this record.
    "candidate_minted": frozenset({"round", "idx", "candidate_id", "parent_id", "label", "source"}),
    # WHO fired WHAT. `payload` is the command's arguments (a steer carries whole prompt
    # fields) and `idempotency_key` is transport.
    "command": frozenset({"command_id", "kind", "issued_by_user_id"}),
    # `effect` is the applied delta — the ack's own bulk, addressable where it landed.
    "command_ack": frozenset({"command_id", "status", "detail"}),
    # The seed's provenance stamp. The rest of it is the origin prompt and the config delta.
    "cycle_seed": frozenset({"seed.origin_source"}),
    # `traceback` is a diagnostic, not a step: it belongs to the one error, not to a window
    # of a thousand records.
    "error": frozenset({"kind", "message", "stop_reason", "round"}),
    # The call's identity and what it COST. Its messages, reasoning and response are the
    # audit twin's alone (`webapp/CLAUDE.md` § Display-data sources) and are 97% of it.
    "llm_call": frozenset(
        {
            "node",
            "round",
            "candidate_idx",
            "payload_kind",
            "call_id",
            "payload.model",
            "payload.duration_s",
            "payload.cached",
            "payload.usage",
        }
    ),
    # Whole: the record IS a reading. `detail` names what the wait is and `call_id` pairs it
    # with its start — the two whose loss is silent rather than visible.
    "llm_call_progress": frozenset({"call_id", "node", "round", "elapsed_s", "detail"}),
    # The `injection_*` breakdown answers the node's LAYOUT, not the chronology, and is two
    # thirds of the record.
    "llm_call_start": frozenset(
        {"call_id", "node", "round", "candidate_idx", "model", "started_at_ms", "prompt_chars"}
    ),
    # `payload.view` is the phase's own bulk and 95% of the record; the round's headline is
    # the one thing a chronology reads off a phase.
    "phase": frozenset({"phase", "event", "round", "payload.round_result"}),
    # `detail` is the warning's structured bulk; the sentence is the step.
    "round_warning": frozenset({"kind", "severity", "message", "round"}),
    # The scalar readings only. `payload.result` is the sample's query, prediction and
    # pipeline data — the round document's — and `payload.scores` carries the candidate's
    # whole prompt fields; together they are 70% of every ledger measured.
    "snapshot": frozenset(
        {
            "event",
            "round",
            "candidate_idx",
            "candidate_total",
            "sample_idx",
            "sample_total",
            "payload.scores.label",
            "payload.scores.accuracy",
            "payload.scores.composite_fitness",
            "payload.result._running.accuracy",
            "payload.result._running.composite_fitness",
        }
    ),
}

_bearing = frozenset(RENDERS_AS_ACTIVITY) - NON_ACTIVITY_KINDS
if frozenset(RAY_PAYLOAD_FIELDS) != _bearing:
    raise RuntimeError(
        "RAY_PAYLOAD_FIELDS must answer for every activity-bearing kind — an unanswered one "
        "rides the chronology as an EMPTY body, which renders as a step that says nothing: "
        f"missing {sorted(_bearing - frozenset(RAY_PAYLOAD_FIELDS))}, "
        f"unbacked {sorted(frozenset(RAY_PAYLOAD_FIELDS) - _bearing)}."
    )

for _kind, _paths in RAY_PAYLOAD_FIELDS.items():
    _fields = frozenset(_arms[_kind].model_fields)
    if _unknown := sorted(p for p in _paths if p.split(".")[0] not in _fields):
        raise RuntimeError(
            f"RAY_PAYLOAD_FIELDS['{_kind}'] names fields {_unknown} that "
            f"{_arms[_kind].__name__} does not declare. A renamed field picks nothing and the "
            "step renders without it — no raise, no log line, just a reading that went blank."
        )
    if _shadowed := sorted(
        p for p in _paths if any(o != p and p.startswith(f"{o}.") for o in _paths)
    ):
        raise RuntimeError(
            f"RAY_PAYLOAD_FIELDS['{_kind}'] declares {_shadowed} under a path that already "
            "takes the whole branch — say one or the other, never both."
        )

del _arms, _bearing, _fields, _kind, _paths, _record_types, _declared, _shadowed, _unknown


def ray_payload(kind: ProjectionKind, record: Mapping[str, Any]) -> dict[str, Any]:
    """One record's ray body: :data:`RAY_PAYLOAD_FIELDS` picked out of its dump, nested paths
    included.

    A path naming nothing THIS record carries yields nothing — an absent key stays absent
    rather than arriving as a null, because the browser's absent-vs-zero rule reads a served
    null as a measured one.
    """
    body: dict[str, Any] = {}
    for path in sorted(RAY_PAYLOAD_FIELDS[kind]):
        _pick(record, path.split("."), body)
    return body


def _pick(src: Mapping[str, Any], path: list[str], dst: dict[str, Any]) -> None:
    head, rest = path[0], path[1:]
    if head not in src:
        return
    value = src[head]
    if not rest:
        dst[head] = value
        return
    if not isinstance(value, Mapping):
        return
    # Grafted on only once the path has found something, and onto whatever a sibling path
    # already built there. An empty ``{}`` left behind is not the same fact as the key being
    # absent — it is the one a renderer's ``?.`` reads as "looked, nothing measured".
    nested = dst.get(head)
    nested = nested if isinstance(nested, dict) else {}
    _pick(value, rest, nested)
    if nested:
        dst[head] = nested


class ProjectionEnvelope(StrictModel):
    """One outbound SSE frame. Frozen wire shape — a receiver MUST treat an unknown field as a DRIFT SIGNAL, not as
    forward-compat slack, and a new field requires the AsyncAPI update first."""

    model_config = ConfigDict(frozen=True)

    kind: ProjectionKind = Field(
        description="Closed-set discriminator; every CycleRecord record_type, plus stream_snapshot.",
    )
    version: int = Field(
        default=1,
        description="Envelope shape version. Bump only on a breaking restructure of this class; payload churn is per-kind.",
    )
    cycle_id: str = Field(
        description="Target cycle the frame describes; redundant with the channel address but stamped per-frame for fan-in demux.",
    )
    sequence: int = Field(
        ge=0,
        description="Ledger offset at append. Live-tail frames carry the record's offset; the leading stream_snapshot frame carries the offset captured at subscribe time.",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-kind body. For record-derived kinds, the record's model_dump; for stream_snapshot, the dashboard.json content + snapshot_at_offset.",
    )
