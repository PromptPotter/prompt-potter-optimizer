"use client";

import type {
  DraftCampaignWire,
  DraftPatch,
  OriginLastResolution,
  RaisedCommand,
} from "@/lib/api";
import { QuestionAnswer } from "./QuestionAnswer";

// The check-in's conversational output: the resolver's plain-language
// assessment, the proposals it left for the operator to click, plus any
// follow-up questions for fields it couldn't confirm on its own. The check-in
// runs automatically on a fresh draft (no "Set up with AI" button) and the
// closed-set config it proposes lives in the optional Advanced expander.
//
// A proposal is offered, never applied. The server derives each one from the
// turn's findings and hands it over already shaped as the `edit-draft-campaign`
// a click will fire, so this panel renders an action rather than reconstructing
// one from a field name.
export function OriginCheckinPanel({
  draft,
  lastResolution,
  raised,
  onApply,
}: {
  draft: DraftCampaignWire;
  lastResolution: OriginLastResolution | null;
  raised: RaisedCommand[];
  onApply: (patch: DraftPatch) => void;
}) {
  const questions = lastResolution?.next_action.questions ?? [];
  if (!lastResolution?.assessment && questions.length === 0 && raised.length === 0) return null;

  return (
    <section className="origin-checkin">
      {lastResolution?.assessment ? (
        <p className="origin-checkin-assessment">{lastResolution.assessment}</p>
      ) : null}
      {raised.length > 0 ? (
        <ul className="origin-raised">
          {raised.map((cmd, i) => (
            <li key={i} className="origin-raised-item">
              <span className="origin-raised-text">
                <span className="origin-raised-label">{proposalLabel(cmd)}</span>
                <span className="origin-raised-evidence">{cmd.evidence}</span>
              </span>
              <button
                type="button"
                className="origin-raised-apply"
                onClick={() => onApply(cmd.payload.patch)}
              >
                Apply
              </button>
            </li>
          ))}
        </ul>
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

function proposalLabel(cmd: RaisedCommand): string {
  const patch = cmd.payload.patch;
  if (patch.column_query) return `Read the input from “${patch.column_query}”`;
  if (patch.column_ground_truth) return `Grade against “${patch.column_ground_truth}”`;
  if (patch.raw_task_description) return `Set the task: “${patch.raw_task_description}”`;
  return "Apply the proposed change";
}
