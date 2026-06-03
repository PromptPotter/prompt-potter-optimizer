"use client";

import type {
  DraftCampaignWire,
  DraftPatch,
  OriginLastResolution,
  ProvenanceSource,
  ProvenanceTag,
} from "@/lib/api";
import { ORIGIN_KEY, type OriginKey } from "@/lib/origin-readiness";
import { ProvenanceBadge, SourceBadge } from "./ProvenanceBadges";
import { QuestionAnswer } from "./QuestionAnswer";

// The closed-set config fields, surfaced so the once-hidden defaults are
// visible + their provenance honest. Each reads its tag from `draft.resolved`.
const CHECKIN_FIELDS: Array<{ key: OriginKey; label: string; value: (d: DraftCampaignWire) => string }> =
  [
    { key: ORIGIN_KEY.taskDescription, label: "Task", value: (d) => d.task_description || "—" },
    { key: ORIGIN_KEY.connector, label: "Backend", value: (d) => d.connector },
    { key: ORIGIN_KEY.scoringComposite, label: "Scoring", value: (d) => d.scoring_composite },
    { key: ORIGIN_KEY.maxRounds, label: "Max rounds", value: (d) => String(d.max_rounds) },
    {
      key: ORIGIN_KEY.optimizerProvider,
      label: "Optimizer",
      value: (d) => `${d.optimizer_provider}${d.optimizer_model ? ` · ${d.optimizer_model}` : ""}`,
    },
  ];

// The origin-setup-in-progress window. Shows the closed-set fields with their
// provenance, an "AI set-up" turn that proposes the task framing + refinements
// (origin-resolution steps 3-4), and the resolver's assessment / questions.
export function OriginCheckinPanel({
  draft,
  resolving,
  lastResolution,
  onResolve,
  onApply,
}: {
  draft: DraftCampaignWire;
  resolving: boolean;
  lastResolution: OriginLastResolution | null;
  onResolve: () => void;
  onApply: (patch: DraftPatch) => void;
}) {
  const ready = CHECKIN_FIELDS.filter(
    (f) => (draft.resolved[f.key] ?? "unset") === "confirmed",
  ).length;
  const questions = lastResolution?.next_action.questions ?? [];

  return (
    <section className="origin-checkin">
      <header className="origin-checkin-head">
        <span className="origin-columns-head">Campaign setup</span>
        <span className="origin-checkin-progress">
          {ready}/{CHECKIN_FIELDS.length} ready
        </span>
      </header>

      <ul className="origin-checkin-fields">
        {CHECKIN_FIELDS.map((f) => {
          const tag: ProvenanceTag = draft.resolved[f.key] ?? "unset";
          const source: ProvenanceSource | undefined = draft.sources[f.key];
          return (
            <li key={f.key} className="origin-checkin-field">
              <span className="origin-checkin-label">{f.label}</span>
              <span className="origin-checkin-value">{f.value(draft)}</span>
              <ProvenanceBadge tag={tag} />
              <SourceBadge source={source} />
            </li>
          );
        })}
      </ul>

      <div className="origin-checkin-actions">
        <button
          type="button"
          className="origin-checkin-resolve"
          disabled={resolving}
          onClick={onResolve}
        >
          {resolving ? "Setting up…" : "Set up with AI"}
        </button>
        <small className="origin-checkin-hint">
          Reads your columns + sample rows to draft the task framing and propose
          any unset fields. High-confidence picks confirm automatically; the
          rest wait for you.
        </small>
      </div>

      {lastResolution?.assessment ? (
        <p className="origin-checkin-assessment">{lastResolution.assessment}</p>
      ) : null}
      {questions.length > 0 ? (
        <ul className="origin-checkin-questions">
          {questions.map((q, i) => (
            <li key={i}>
              <QuestionAnswer question={q} draft={draft} onApply={onApply} />
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
