"use client";

import { useState } from "react";
import type { DraftPatch } from "@/lib/api";
import { cx } from "@/lib/cx";
import { PROMPT_STRING_FIELDS, promptFieldLabel } from "@/lib/prompt-fields";
import { LockButton } from "./NodeConfigEditor";

// The starting prompt's six string fields (`draft.origin_prompt_fields`), edited in place and
// persisted via `edit-draft-campaign` on blur.

// A field's label is not here: `promptFieldLabel` owns it, shared with the run card's diff summary.
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
  locks,
  onLock,
}: {
  value: Record<string, unknown>;
  onApply?: (patch: DraftPatch) => void;
  // Each field's lock off its SERVED row (a `param_keys` membership like any param's).
  locks?: Record<string, boolean>;
  onLock?: (field: string, locked: boolean) => void;
  readOnly?: boolean;
  // Empty fields drop only when also `readOnly`: hiding a typeable slot makes the prompt look shorter.
  compact?: boolean;
}) {
  // `ui/CommitInput`'s two latches over a record: `prevFp` is "the prop moved", `sentFp` "what was
  // last handed up". Not CommitInput itself, since all six fields merge into ONE patch.
  const fingerprint = JSON.stringify(asStrings(value));
  const [prevFp, setPrevFp] = useState(fingerprint);
  const [sentFp, setSentFp] = useState(fingerprint);
  const [fields, setFields] = useState<Record<string, string>>(() => asStrings(value));
  if (fingerprint !== prevFp) {
    setPrevFp(fingerprint);
    setSentFp(fingerprint);
    setFields(asStrings(value));
  }

  const setField = (key: string, v: string) => setFields((prev) => ({ ...prev, [key]: v }));

  // Never re-send a value already sent: each patch is a CommandRecord on the check-in ledger.
  const commit = () => {
    if (readOnly || !onApply) return;
    // Through `asStrings` so both sides of the compare are keyed in `FIELDS` order.
    const draftFp = JSON.stringify(asStrings(fields));
    if (draftFp === sentFp) return;
    setSentFp(draftFp);
    const merged: Record<string, unknown> = { ...value };
    for (const f of FIELDS) merged[f.key] = fields[f.key];
    onApply({ origin_prompt_fields: merged });
  };

  const fewShot = Array.isArray(value.few_shot_examples)
    ? (value.few_shot_examples as unknown[]).length
    : 0;

  const shown = compact && readOnly ? FIELDS.filter((f) => (fields[f.key] ?? "").trim()) : FIELDS;

  return (
    <section className={cx("prompt-editor", compact && "is-compact")}>
      <span className="prompt-editor-title">Starting prompt</span>
      <div className="prompt-editor-grid">
        {shown.map((f) => {
          const locked = locks?.[f.key];
          return (
            // A div, not a label: the lock beside the name must not join the box's accessible name.
            <div key={f.key} className="prompt-field">
              <span className="prompt-field-label">
                {f.label}
                {onLock && locked !== undefined ? (
                  <LockButton
                    locked={locked}
                    readOnly={readOnly}
                    onClick={() => onLock(f.key, !locked)}
                  />
                ) : null}
              </span>
              <textarea
                className="prompt-field-input"
                aria-label={f.label}
                rows={compact ? 2 : f.rows}
                value={fields[f.key]}
                placeholder={f.hint}
                readOnly={readOnly}
                disabled={readOnly}
                onChange={(e) => setField(f.key, e.target.value)}
                onBlur={commit}
              />
            </div>
          );
        })}
      </div>
      {fewShot > 0 ? (
        <p className="prompt-editor-fewshot">
          + {fewShot} few-shot example{fewShot === 1 ? "" : "s"} (kept as-is)
        </p>
      ) : null}
    </section>
  );
}
