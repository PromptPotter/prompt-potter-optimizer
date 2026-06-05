"use client";

import { useState } from "react";
import type { DraftCampaignWire, DraftPatch, OriginQuestion } from "@/lib/api";
import { questionOptions, questionPatch } from "@/lib/origin-readiness";

// One answerable resolver question. Renders the prompt plus a control keyed to
// the field's answer set: a picker when the resolver gave `options` (or for a
// column-mapping question, the uploaded headers), else a free-text input.
// Submitting maps the answer to an `edit-draft-campaign` patch via
// `questionPatch` (server flips the field CONFIRMED) — the answer-back half of
// the resolver loop. A field that isn't string-applicable yields no patch and
// the control is omitted (only its prompt shows).
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
  // "1" is a valid probe across every mapped field (incl. max_rounds' numeric
  // guard); only backend.node_config / unknown fields yield null → not answerable.
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
