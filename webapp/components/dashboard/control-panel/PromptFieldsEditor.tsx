"use client";

import { useState } from "react";
import type { DraftPatch } from "@/lib/api";

// The starting-prompt control panel. The check-in's decomposition (or an
// authored dataset's prompt) lands on `draft.starting_prompt` as a
// PromptTemplate field dict; this surfaces the six string fields as editable
// text. Non-demo edits persist via `edit-draft-campaign` on blur; in demo the
// edits stay local (reset on Start) and a note says so.

// The six keys MUST stay in sync with `PROMPT_STRING_FIELDS` — the source of
// truth at `promptpotter/config/settings.py`. They cross the TS/Py seam, so a
// rename there must land here too.
const FIELDS: ReadonlyArray<{ key: string; label: string; hint: string; rows: number }> = [
  { key: "persona", label: "Persona", hint: "Who the model should act as", rows: 2 },
  { key: "task_intent", label: "Task intent", hint: "The goal, in one line", rows: 2 },
  { key: "problem_description", label: "Problem", hint: "What each input is", rows: 2 },
  { key: "instruction", label: "Instructions", hint: "The directive the model follows", rows: 3 },
  { key: "thinking_style", label: "Thinking style", hint: "How to reason before answering", rows: 2 },
  { key: "answer_format", label: "Answer format", hint: "Exact shape of the output", rows: 2 },
];

function asStrings(value: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of FIELDS) {
    const v = value[f.key];
    out[f.key] = typeof v === "string" ? v : "";
  }
  return out;
}

export function PromptFieldsEditor({
  value,
  demo,
  onApply,
  flat = false,
  readOnly = false,
}: {
  value: Record<string, unknown>;
  demo: boolean;
  onApply?: (patch: DraftPatch) => void;
  // When true, render without the own card chrome (it nests inside another
  // card — the unified setup-preview panel).
  flat?: boolean;
  // When true, disable every field — the minted-campaign node panel shows the
  // origin prompt read-only (no draft to persist to).
  readOnly?: boolean;
}) {
  // Fingerprint the incoming prompt; render-phase reset when it changes (e.g.
  // the check-in just populated it) so external updates flow in without a stale
  // frame. Local edits between resets are the operator's working copy.
  const fingerprint = JSON.stringify(asStrings(value));
  const [prevFp, setPrevFp] = useState(fingerprint);
  const [fields, setFields] = useState<Record<string, string>>(() => asStrings(value));
  if (fingerprint !== prevFp) {
    setPrevFp(fingerprint);
    setFields(asStrings(value));
  }

  const setField = (key: string, v: string) => setFields((prev) => ({ ...prev, [key]: v }));

  // Persist on blur — never in demo or readonly (edits are visual; mint uses
  // the dataset's prompt). Merge over `value` so few_shot_examples / plan
  // survive untouched.
  const commit = () => {
    if (demo || readOnly || !onApply) return;
    const merged: Record<string, unknown> = { ...value };
    for (const f of FIELDS) merged[f.key] = fields[f.key];
    onApply({ starting_prompt: merged });
  };

  const fewShot = Array.isArray(value.few_shot_examples)
    ? (value.few_shot_examples as unknown[]).length
    : 0;

  return (
    <section className={flat ? "prompt-editor is-flat" : "prompt-editor"}>
      {flat ? (
        <span className="prompt-editor-title">Starting prompt</span>
      ) : (
        <header className="prompt-editor-head">
          <span className="prompt-editor-title">Starting prompt</span>
          <span className="prompt-editor-sub">the prompt the optimizer evolves — edit any field</span>
        </header>
      )}
      {demo && !flat ? (
        <p className="prompt-editor-demo-note" role="note">
          Demo mode — changes here reset when you start the campaign.
        </p>
      ) : null}
      <div className="prompt-editor-grid">
        {FIELDS.map((f) => (
          <label key={f.key} className="prompt-field">
            <span className="prompt-field-label">{f.label}</span>
            <textarea
              className="prompt-field-input"
              rows={f.rows}
              value={fields[f.key]}
              placeholder={f.hint}
              readOnly={readOnly}
              disabled={readOnly}
              onChange={(e) => setField(f.key, e.target.value)}
              onBlur={commit}
            />
          </label>
        ))}
      </div>
      {fewShot > 0 ? (
        <p className="prompt-editor-fewshot">
          + {fewShot} few-shot example{fewShot === 1 ? "" : "s"} (kept as-is)
        </p>
      ) : null}
    </section>
  );
}
