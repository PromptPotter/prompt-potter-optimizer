"use client";

import type { DraftCampaignWire, DraftPatch, OriginLastResolution } from "@/lib/api";
import { QuestionAnswer } from "./QuestionAnswer";

// The check-in's conversational output: the resolver's plain-language
// assessment plus any follow-up questions for fields it couldn't confirm on
// its own. The check-in runs automatically on a fresh draft (no "Set up with
// AI" button) and the closed-set config it proposes lives in the optional
// Advanced expander — this panel is only the assessment + answer-back loop.
export function OriginCheckinPanel({
  draft,
  lastResolution,
  onApply,
}: {
  draft: DraftCampaignWire;
  lastResolution: OriginLastResolution | null;
  onApply: (patch: DraftPatch) => void;
}) {
  const questions = lastResolution?.next_action.questions ?? [];
  if (!lastResolution?.assessment && questions.length === 0) return null;

  return (
    <section className="origin-checkin">
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
