"use client";

import { useState } from "react";
import type { DraftCampaignWire, DraftPatch, OriginQuestion } from "@/lib/api";
import { questionOptions, questionPatch } from "@/lib/origin-readiness";

// One resolver question and its answer control; the server flips the answered field CONFIRMED.
export function QuestionAnswer({
  question,
  draft,
  onApply,
}: {
  question: OriginQuestion;
  draft: DraftCampaignWire;
  onApply: (patch: DraftPatch) => void;
}) {
  const [text, setText] = useState("");
  const options = questionOptions(question.field, question.options, draft.headers);
  // "1" is a valid probe for every mapped field, max_rounds included; only unmapped ones yield null.
  const answerable = questionPatch(question.field, "1") !== null;

  const submit = (answer: string) => {
    const patch = questionPatch(question.field, answer);
    if (patch) onApply(patch);
  };

  return (
    <div className="origin-question">
      <span className="origin-question-prompt">{question.prompt}</span>
      {!answerable ? null : options.length > 0 ? (
        <select
          className="origin-question-select"
          defaultValue=""
          onChange={(e) => submit(e.target.value)}
        >
          <option value="" disabled>
            — choose —
          </option>
          {options.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      ) : (
        <span className="origin-question-text">
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Your answer"
          />
          <button type="button" disabled={!text.trim()} onClick={() => submit(text)}>
            Answer
          </button>
        </span>
      )}
    </div>
  );
}
