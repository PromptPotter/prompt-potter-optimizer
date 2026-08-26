"use client";

import { useState } from "react";
import type { DraftPatch } from "@/lib/api";
import { cx } from "@/lib/cx";
import { PROMPT_STRING_FIELDS, promptFieldLabel } from "@/lib/prompt-fields";

// The starting-prompt control panel. The check-in's decomposition (or an
// authored dataset's prompt) lands on `draft.origin_prompt_fields` as a
// PromptTemplate field dict; this surfaces the six string fields as editable
// text. Edits persist via `edit-draft-campaign` on blur, unless `readOnly`
// (the minted-campaign node panel shows the origin prompt with no draft to
// write to).

// AUTHORING layout per field — the placeholder hint and how tall the box starts.
// The field's NAME is not here: `promptFieldLabel` owns it, beside the canonical
// key list (the TS/Py seam, `lib/prompt-fields.ts`), because the run card's
// "changed vs origin" summary names the same fields and two labels for one field is
// a synonym, not a second fact. Render order + membership come from that list too.
const FIELD_META: Record<string, { hint: string; rows: number }> = {
  persona: { hint: "Who the model should act as", rows: 2 },
  task_intent: { hint: "The goal, in one line", rows: 2 },
  problem_description: { hint: "What each input is", rows: 2 },
  instruction: { hint: "The directive the model follows", rows: 3 },
  thinking_style: { hint: "How to reason before answering", rows: 2 },
  answer_format: { hint: "Exact shape of the output", rows: 2 },
};
const FIELDS = PROMPT_STRING_FIELDS.map((key) => ({
  key,
  label: promptFieldLabel(key),
  ...FIELD_META[key],
}));

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
  onApply,
  readOnly = false,
  compact = false,
}: {
  value: Record<string, unknown>;
  onApply?: (patch: DraftPatch) => void;
  // When true, disable every field — the minted-campaign node panel shows the
  // origin prompt read-only (no draft to persist to).
  readOnly?: boolean;
  // Half-width host (the chat run card): shorter boxes, and — read-only only —
  // empty fields dropped. An empty slot is an affordance while AUTHORING and pure
  // noise while inspecting, so the drop is gated on `readOnly`, never on `compact`
  // alone; hiding a typeable slot would make the prompt look shorter than it is.
  compact?: boolean;
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

  // Persist on blur — never when readonly. Merge over `value` so
  // few_shot_examples / plan survive untouched.
  const commit = () => {
    if (readOnly || !onApply) return;
    const merged: Record<string, unknown> = { ...value };
    for (const f of FIELDS) merged[f.key] = fields[f.key];
    onApply({ origin_prompt_fields: merged });
  };

  const fewShot = Array.isArray(value.few_shot_examples)
    ? (value.few_shot_examples as unknown[]).length
    : 0;

  const shown = compact && readOnly ? FIELDS.filter((f) => (fields[f.key] ?? "").trim()) : FIELDS;

  // Draws no frame: `NodeSurface` is its only caller and already provides one.
  return (
    <section className={cx("prompt-editor", compact && "is-compact")}>
      <span className="prompt-editor-title">Starting prompt</span>
      <div className="prompt-editor-grid">
        {shown.map((f) => (
          <label key={f.key} className="prompt-field">
            <span className="prompt-field-label">{f.label}</span>
            <textarea
              className="prompt-field-input"
              rows={compact ? 2 : f.rows}
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
