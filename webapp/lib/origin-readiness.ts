// Client-side mirror of the server's `origin_readiness` checklist
// (`promptpotter/application/datasets/origin_readiness.py`). The draft wire
// carries the inputs the gate decides over — `field_provenance`,
// `headers`, and the `column_query` / `column_ground_truth` values — but not
// the gap list itself, so the panel re-derives it here to render the
// required-first tier *before* the operator clicks Create. It is a faithful
// projection of shipped wire fields, not new policy: a column is satisfied
// iff its provenance is `confirmed` AND its value is a member of `headers`,
// exactly as `_check_column` decides server-side. The `422 origin_incomplete`
// gaps remain authoritative; this only drives the proactive UI.

import type { DraftCampaignWire, DraftPatch, OriginGap, ProvenanceTag } from "./api";

// The dotted field-key namespace the server's `origin_readiness` checklist
// keys `draft.field_provenance` by. Single source for every site that reads provenance
// by key (this module, plus the ingest column-mapping and check-in panels).
// Mirror of the field ids in `origin_readiness.py`. Config keys are still
// listed for `questionPatch` (a resolver question can set them), but they are
// NOT gated — config carries a default the operator edits.
export const ORIGIN_KEY = {
  columnQuery: "column.query",
  columnGroundTruth: "column.ground_truth",
  taskDescription: "task_description",
  connector: "connector",
  scoringComposite: "scoring_composite",
  maxRounds: "max_rounds",
  backendNodeConfig: "backend.node_config",
} as const;

export interface OriginReadiness {
  complete: boolean;
  gaps: OriginGap[];
}

interface ColumnCheck {
  fieldKey: string;
  value: string;
  label: string; // operator-facing role: "input" / "target"
}

function checkColumn(draft: DraftCampaignWire, check: ColumnCheck): OriginGap | null {
  const provenance: ProvenanceTag = draft.field_provenance[check.fieldKey] ?? "unset";
  if (provenance === "confirmed" && draft.headers.includes(check.value)) {
    return null;
  }
  if (provenance === "proposed") {
    return {
      field: check.fieldKey,
      reason: "proposed_unconfirmed",
      hint: `Confirm the ${check.label} column (proposed ${JSON.stringify(check.value)}).`,
    };
  }
  const headers = draft.headers.join(", ") || "<none>";
  return {
    field: check.fieldKey,
    reason: "unset",
    hint: `Pick which uploaded column is the ${check.label}. Available: ${headers}.`,
  };
}

// `task_description` is the one non-column field that gates: it has no default
// framing, so it stays open until the operator (or the resolver, high-confidence)
// states it. Config (connector / scorer / round cap / optimizer LLM / node
// overlay) is NOT gated — each carries a default the operator edits. Mirror of
// `origin_readiness.py`.
const TASK_DESCRIPTION = {
  fieldKey: ORIGIN_KEY.taskDescription,
  hint: "Describe what the prompt should do.",
};

function checkConfirmed(
  draft: DraftCampaignWire,
  field: { fieldKey: string; hint: string },
): OriginGap | null {
  const provenance: ProvenanceTag = draft.field_provenance[field.fieldKey] ?? "unset";
  if (provenance === "confirmed") return null;
  return {
    field: field.fieldKey,
    reason: provenance === "proposed" ? "proposed_unconfirmed" : "unset",
    hint: field.hint,
  };
}

// The gated set: the column mapping (header-membership-checked) plus the task
// framing. Config is not gated. A faithful projection of the shipped wire
// fields; the `422 origin_incomplete` gaps (incl. answer_space) remain
// authoritative.
export function originReadiness(draft: DraftCampaignWire): OriginReadiness {
  const gaps: OriginGap[] = [];
  for (const check of [
    { fieldKey: ORIGIN_KEY.columnQuery, value: draft.column_query, label: "input" },
    { fieldKey: ORIGIN_KEY.columnGroundTruth, value: draft.column_ground_truth, label: "target" },
  ]) {
    const gap = checkColumn(draft, check);
    if (gap) gaps.push(gap);
  }
  const taskGap = checkConfirmed(draft, TASK_DESCRIPTION);
  if (taskGap) gaps.push(taskGap);
  return { complete: gaps.length === 0, gaps };
}

// Map a resolver question's checklist `field` id + the operator's answer onto
// an `edit-draft-campaign` patch — the answer-back half of the resolver loop
// (spec § The loop step 2: an `ask` answer applies as a confirmed patch).
// Returns null for a field that can't be set from a string answer
// (`backend.node_config`) or a malformed numeric answer, so the caller skips
// it rather than sending a bad patch. The server flips the field CONFIRMED +
// STATED on apply.
export function questionPatch(field: string, answer: string): DraftPatch | null {
  const value = answer.trim();
  if (!value) return null;
  switch (field) {
    case ORIGIN_KEY.columnQuery:
      return { column_query: value };
    case ORIGIN_KEY.columnGroundTruth:
      return { column_ground_truth: value };
    case ORIGIN_KEY.taskDescription:
      return { raw_task_description: value };
    case ORIGIN_KEY.connector:
      return { connector: value };
    case ORIGIN_KEY.scoringComposite:
      return { scoring_composite: value };
    case ORIGIN_KEY.maxRounds: {
      const n = Number(value);
      return Number.isInteger(n) && n >= 1 && n <= 100
        ? { optimization_overrides: { max_rounds: n } }
        : null;
    }
    default:
      return null; // backend.node_config + unknown fields aren't string-applicable here
  }
}

// The answer set for a question: the resolver's own `options` when it supplied
// them, else the uploaded headers for a column-mapping question (so the picker
// is always grounded in real columns), else empty → free-text input.
export function questionOptions(field: string, options: string[], headers: string[]): string[] {
  if (options.length > 0) return options;
  if (field === ORIGIN_KEY.columnQuery || field === ORIGIN_KEY.columnGroundTruth) return headers;
  return [];
}

// Friendly names for the registered connector / scorer slugs. Anything not in
// the map renders its raw slug — honest, never a fabricated label.
const CONNECTOR_LABELS: Record<string, string> = { termnorm: "the TermNorm pipeline" };
const SCORER_LABELS: Record<string, string> = {
  exact_match: "an exact match against the target",
};

// The first line of a task description, stripped of markdown heading markers —
// used as a short human name for the task. Returns null when there is no clean
// short title (empty, or a full spec/sentence line that would dump): the recap
// then falls back to the column-based phrasing. Task descriptions are often a
// full markdown brief, so we never splice the raw body into the sentence.
function shortTaskTitle(task: string): string | null {
  const firstLine = task
    .split("\n")
    .map((l) => l.trim())
    .find((l) => l.length > 0);
  if (!firstLine) return null;
  const cleaned = firstLine.replace(/^#+\s*/, "").replace(/[.\s]+$/, "").trim();
  return cleaned.length > 0 && cleaned.length <= 72 ? cleaned : null;
}

// A jargon-free, one-line restatement of what the campaign will do, built from
// the confirmed draft fields — the operator approves *intent*, not field names
// (VOICE register: anti-nerdy, accessibility-first). Pending the LLM
// resolver's authored `ready`-turn recap (origin-resolution step 4); until then
// this is a deterministic restatement of the draft's own values. Kept to a
// single short sentence — never the raw task brief, which can be a full doc.
export function plainLanguageRecap(draft: DraftCampaignWire): string {
  const input = draft.column_query || "your input";
  const target = draft.column_ground_truth || "the target";
  const scorer = SCORER_LABELS[draft.scoring_composite] ?? draft.scoring_composite;
  const connector = CONNECTOR_LABELS[draft.connector] ?? draft.connector;
  const maxRounds = draft.optimization_overrides.max_rounds;
  const rounds = maxRounds === 1 ? "1 round" : `up to ${maxRounds} rounds`;

  const title = shortTaskTitle(draft.raw_task_description);
  const lead = title
    ? `evolve a prompt for “${title}”`
    : `evolve a prompt that turns each “${input}” into the right “${target}”`;

  return `PromptPotter will ${lead} — running ${connector}, scoring by ${scorer}, over ${rounds}.`;
}
