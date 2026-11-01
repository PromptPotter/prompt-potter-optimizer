"use client";

import type { DraftPatch, OriginLastResolution, RaisedCommand } from "@/lib/api";
import { QuestionAnswer } from "./QuestionAnswer";

// A proposal arrives already shaped as the `edit-draft-campaign` a click fires, worded — never rebuild one.
export function OriginCheckinPanel({
  lastResolution,
  raised,
  onApply,
}: {
  lastResolution: OriginLastResolution | null;
  raised: RaisedCommand[];
  onApply: (patch: DraftPatch) => void;
}) {
  const questions = lastResolution?.questions ?? [];
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
            {cmd.label} <small>{cmd.evidence}</small>{" "}
            <button type="button" onClick={() => onApply(cmd.payload.patch)}>
              Apply
            </button>
          </li>
        ))}
        {questions.map((q, i) => (
          <li key={`q${i}`}>
            <QuestionAnswer question={q} onApply={onApply} />
          </li>
        ))}
      </ul>
    </section>
  );
}
