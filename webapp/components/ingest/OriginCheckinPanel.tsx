"use client";

import type {
  DraftCampaignWire,
  DraftPatch,
  OriginLastResolution,
  RaisedCommand,
} from "@/lib/api";
import { QuestionAnswer } from "./QuestionAnswer";

// A proposal arrives already shaped as the `edit-draft-campaign` a click fires — never rebuild one.
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
    <section>
      {lastResolution?.assessment ? (
        <p>
          <i>{lastResolution.assessment}</i>
        </p>
      ) : null}
      <ul>
        {raised.map((cmd, i) => (
          <li key={i}>
            {proposalLabel(cmd)} <small>{cmd.evidence}</small>{" "}
            <button type="button" onClick={() => onApply(cmd.payload.patch)}>
              Apply
            </button>
          </li>
        ))}
        {questions.map((q, i) => (
          <li key={`q${i}`}>
            <QuestionAnswer question={q} draft={draft} onApply={onApply} />
          </li>
        ))}
      </ul>
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
