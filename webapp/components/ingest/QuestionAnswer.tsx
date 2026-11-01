"use client";

import { useState } from "react";
import type { CheckinQuestion, DraftPatch } from "@/lib/api";
import { setIn } from "./SchemaFields";

// A served question carries the patch path its answer is written at, and the answers it takes.
export function QuestionAnswer({
  question,
  onApply,
}: {
  question: CheckinQuestion;
  onApply: (patch: DraftPatch) => void;
}) {
  const [text, setText] = useState("");

  const submit = (answer: string) => {
    const value = answer.trim();
    if (value) onApply(setIn({}, question.path, value));
  };

  return (
    <>
      {question.prompt}{" "}
      {question.options.length > 0 ? (
        <select defaultValue="" onChange={(e) => submit(e.target.value)}>
          <option value="" disabled>
            — choose —
          </option>
          {question.options.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      ) : (
        <>
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Your answer"
            aria-label="Your answer"
          />{" "}
          <button type="button" disabled={!text.trim()} onClick={() => submit(text)}>
            Answer
          </button>
        </>
      )}
    </>
  );
}
