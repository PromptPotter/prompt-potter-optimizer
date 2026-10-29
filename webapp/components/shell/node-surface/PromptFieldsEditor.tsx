"use client";

import { useState } from "react";
import type { DraftPatch, NodePrompt } from "@/lib/api";
import { cx } from "@/lib/cx";
import { promptField } from "@/lib/prompt-fields";
import { LockButton } from "./NodeConfigEditor";

function asStrings(keys: readonly string[], value: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const key of keys) out[key] = value[key] ?? "";
  return out;
}

// One prompt node's loci. `fields` is the roster the node declares: its served `prompt` rows.
export function PromptFieldsEditor({
  node,
  prompts,
  fields: keys,
  onApply,
  readOnly = false,
  compact = false,
  locks,
  onLock,
}: {
  node: string;
  prompts: Record<string, NodePrompt>;
  fields: readonly string[];
  onApply: ((patch: DraftPatch) => void) | undefined;
  locks: Record<string, boolean>;
  onLock: ((field: string, locked: boolean) => void) | undefined;
  readOnly?: boolean;
  compact?: boolean | undefined;
}) {
  const own = prompts[node];
  const value = own?.fields ?? {};
  // `ui/CommitInput`'s two latches, not CommitInput itself: every field merges into ONE patch.
  const fingerprint = JSON.stringify(asStrings(keys, value));
  const [prevFp, setPrevFp] = useState(fingerprint);
  const [sentFp, setSentFp] = useState(fingerprint);
  const [draft, setDraft] = useState<Record<string, string>>(() => asStrings(keys, value));
  if (fingerprint !== prevFp) {
    setPrevFp(fingerprint);
    setSentFp(fingerprint);
    setDraft(asStrings(keys, value));
  }

  const setField = (key: string, v: string) => setDraft((prev) => ({ ...prev, [key]: v }));

  // Never re-sends a sent value: each patch is a CommandRecord on the draft's ledger.
  const commit = () => {
    if (readOnly || !onApply) return;
    const draftFp = JSON.stringify(asStrings(keys, draft));
    if (draftFp === sentFp) return;
    setSentFp(draftFp);
    // The whole map, every other node as it stands: the patch REPLACES the draft's `origin_prompts`.
    const next: Record<string, Record<string, string>> = {};
    for (const [name, p] of Object.entries(prompts)) next[name] = p.fields;
    next[node] = Object.fromEntries(
      Object.entries(asStrings(keys, draft)).filter(([, v]) => v.trim() !== ""),
    );
    onApply({ origin_prompts: next });
  };

  const fewShot = own?.shots.length ?? 0;
  const rows = keys.map((key) => ({ key, ...promptField(key) }));
  const shown = compact && readOnly ? rows.filter((f) => (draft[f.key] ?? "").trim()) : rows;

  return (
    <section className={cx("prompt-editor", compact && "is-compact")}>
      <span className="prompt-editor-title">
        {readOnly ? "Prompt, as banked" : "Starting prompt"}
        {Object.keys(prompts).length > 1 ? <code className="opt-detail-id">{node}</code> : null}
      </span>
      <div className="prompt-editor-grid">
        {shown.map((f) => {
          const locked = locks[f.key];
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
                rows={compact ? 2 : (f.rows ?? 2)}
                value={draft[f.key] ?? ""}
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
          + {fewShot} shot{fewShot === 1 ? "" : "s"} (kept as-is)
        </p>
      ) : null}
    </section>
  );
}
