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

import type { DraftCampaignWire, OriginGap, ProvenanceTag } from "./api";

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

// The column mapping is the only gated field today. The remaining closed-set
// fields (task framing, connector/scorer/round-cap/model provenance) join the
// gate when the LLM resolver (origin-resolution steps 3-4) lands.
export function originReadiness(draft: DraftCampaignWire): OriginReadiness {
  const gaps: OriginGap[] = [];
  for (const check of [
    { fieldKey: "column.query", value: draft.column_query, label: "input" },
    { fieldKey: "column.ground_truth", value: draft.column_ground_truth, label: "target" },
  ]) {
    const gap = checkColumn(draft, check);
    if (gap) gaps.push(gap);
  }
  return { complete: gaps.length === 0, gaps };
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
