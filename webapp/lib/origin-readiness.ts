// Origin check-in client helpers: the resolver answer-back loop (`questionPatch`
// / `questionOptions`) and the jargon-free `plainLanguageRecap`. The mint GATE
// itself is server-owned: `origin_readiness.py` is the single checklist (columns,
// task framing, node models — no individual prompt field is gated), and its
// verdict rides every draft response as `draft.readiness` (the UI reads that, it
// does NOT re-derive — the node-model check can't be mirrored faithfully in the
// client and a partial mirror only drifts).

import type { DraftCampaignWire, DraftPatch } from "./api";

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
      // 0 is legal and load-bearing — "measure the origin and stop" (server: ge=0). A `>= 1`
      // guard silently dropped the patch, so the draft kept its previous ceiling and the
      // operator who asked for zero rounds got the default number of them.
      const n = Number(value);
      return Number.isInteger(n) && n >= 0 && n <= 100
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
