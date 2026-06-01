// Client-side mirror of the server's `origin_readiness` checklist
// (`promptpotter/application/datasets/origin_readiness.py`). The draft wire
// carries the inputs the gate decides over — `resolved` provenance,
// `headers`, and the `column_query` / `column_ground_truth` values — but not
// the gap list itself, so the panel re-derives it here to render the
// required-first tier *before* the operator clicks Create. It is a faithful
// projection of shipped wire fields, not new policy: a column is satisfied
// iff its provenance is `confirmed` AND its value is a member of `headers`,
// exactly as `_check_column` decides server-side. The `422 origin_incomplete`
// gaps remain authoritative; this only drives the proactive UI.

import type { DraftCampaignWire, DraftPatch, OriginGap, ProvenanceTag } from "./api";

// The dotted field-key namespace the server's `origin_readiness` checklist
// keys `draft.resolved` / `draft.sources` by. Single source for every site
// that reads provenance by key (this module, plus the ingest column-mapping
// and check-in panels). Mirror of the field ids in `origin_readiness.py`.
export const ORIGIN_KEY = {
  columnQuery: "column.query",
  columnGroundTruth: "column.ground_truth",
  taskDescription: "task_description",
  connector: "connector",
  scoringComposite: "scoring_composite",
  maxRounds: "max_rounds",
  optimizerProvider: "optimizer.provider",
  optimizerModel: "optimizer.model",
  backendNodeConfig: "backend.node_config",
} as const;

export type OriginKey = (typeof ORIGIN_KEY)[keyof typeof ORIGIN_KEY];

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
  const provenance: ProvenanceTag = draft.resolved[check.fieldKey] ?? "unset";
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

// The closed-set config knobs (beyond the column mapping). Each is satisfied by
// `confirmed` provenance alone — its value seeds from a template default and
// auto-confirms at ingest, so the operator overrides rather than fills them.
// `task_description` is the one that lands `unset` (no default framing), so it
// gates until the operator (or the resolver, high-confidence) states it. Mirror
// of `_CONFIG_FIELDS` in `origin_readiness.py`.
const CONFIG_FIELDS: Array<{ fieldKey: OriginKey; hint: string }> = [
  { fieldKey: ORIGIN_KEY.taskDescription, hint: "Describe what the prompt should do." },
  { fieldKey: ORIGIN_KEY.connector, hint: "Confirm which backend runs the pipeline." },
  { fieldKey: ORIGIN_KEY.scoringComposite, hint: "Confirm how a prediction is scored." },
  { fieldKey: ORIGIN_KEY.maxRounds, hint: "Confirm the optimization round cap." },
  { fieldKey: ORIGIN_KEY.optimizerProvider, hint: "Confirm the optimizer LLM provider." },
  { fieldKey: ORIGIN_KEY.optimizerModel, hint: "Confirm the optimizer LLM model." },
  { fieldKey: ORIGIN_KEY.backendNodeConfig, hint: "Confirm the backend node overlay." },
];

function checkConfirmed(
  draft: DraftCampaignWire,
  field: { fieldKey: string; hint: string },
): OriginGap | null {
  const provenance: ProvenanceTag = draft.resolved[field.fieldKey] ?? "unset";
  if (provenance === "confirmed") return null;
  return {
    field: field.fieldKey,
    reason: provenance === "proposed" ? "proposed_unconfirmed" : "unset",
    hint: field.hint,
  };
}

// The full closed set: the column mapping (header-membership-checked) plus the
// once-hidden config defaults. A faithful projection of the shipped wire fields;
// the `422 origin_incomplete` gaps remain authoritative.
export function originReadiness(draft: DraftCampaignWire): OriginReadiness {
  const gaps: OriginGap[] = [];
  for (const check of [
    { fieldKey: ORIGIN_KEY.columnQuery, value: draft.column_query, label: "input" },
    { fieldKey: ORIGIN_KEY.columnGroundTruth, value: draft.column_ground_truth, label: "target" },
  ]) {
    const gap = checkColumn(draft, check);
    if (gap) gaps.push(gap);
  }
  for (const field of CONFIG_FIELDS) {
    const gap = checkConfirmed(draft, field);
    if (gap) gaps.push(gap);
  }
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
      return { task_description: value };
    case ORIGIN_KEY.connector:
      return { connector: value };
    case ORIGIN_KEY.scoringComposite:
      return { scoring_composite: value };
    case ORIGIN_KEY.optimizerProvider:
      return { optimizer_provider: value };
    case ORIGIN_KEY.optimizerModel:
      return { optimizer_model: value };
    case ORIGIN_KEY.maxRounds: {
      const n = Number(value);
      return Number.isInteger(n) && n >= 1 && n <= 100 ? { max_rounds: n } : null;
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

// A jargon-free restatement of what the campaign will do, built from the
// confirmed draft fields — the operator approves *intent*, not field names
// (.impeccable register: anti-nerdy, accessibility-first). Pending the LLM
// resolver's authored `ready`-turn recap (origin-resolution step 4); until
// then this is a deterministic restatement of the draft's own values.
export function plainLanguageRecap(draft: DraftCampaignWire): string {
  const input = draft.column_query || "your input";
  const target = draft.column_ground_truth || "the target";
  const scorer = SCORER_LABELS[draft.scoring_composite] ?? draft.scoring_composite;
  const connector = CONNECTOR_LABELS[draft.connector] ?? draft.connector;
  const model = draft.optimizer_model || "the default model";
  const rounds = draft.max_rounds === 1 ? "1 round" : `up to ${draft.max_rounds} rounds`;

  const lead = draft.task_description.trim()
    ? draft.task_description.trim()
    : `evolve a prompt that turns each “${input}” into the right “${target}”`;

  return (
    `You're about to ${lead}. PromptPotter will run ${connector}, ` +
    `scoring each answer by ${scorer}, and refine the prompt with ${model} over ${rounds}.`
  );
}
