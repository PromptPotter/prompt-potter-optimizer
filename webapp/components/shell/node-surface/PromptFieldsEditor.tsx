"use client";

import { useImperativeHandle, useMemo, useRef, useState, type DragEvent, type Ref } from "react";
import { libraryRead, postCommand, type DraftPatch, type NodePrompt } from "@/lib/api";
import { cx } from "@/lib/cx";
import { segmentPrompt, type KnownSnippet } from "@/lib/derivations";
import { useCommand } from "@/lib/hooks/useCommand";
import { useRead } from "@/lib/hooks/useRead";
import { invalidateReads } from "@/lib/read-cache";
import { useWorkspace } from "@/lib/workspace";
import { PromptSegments } from "./PromptSegments";
import { promptField, promptFieldsPatch, withSnippet } from "@/lib/prompt-fields";
import type { NodeScope } from "@/lib/SelectionContext";
import { endSnippetDrag, useDraggedSnippet } from "@/lib/state/snippet-drag";
import { FailureLine, Term } from "@/components/ui";
import { LockButton } from "./NodeConfigEditor";

function asStrings(keys: readonly string[], value: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const key of keys) out[key] = value[key] ?? "";
  return out;
}

// A JSON body or a fenced block reads by its columns: it takes the monospace face.
const STRUCTURED = /^\s*[{[]|```/;

// A snippet tapped in a library lands as a drop does: after the field's own text, committed at once.
export interface PromptFieldsHandle {
  insert: (field: string, text: string) => void;
}

// One prompt node's loci. `fields` is the roster the node declares: its served `prompt` rows.
export function PromptFieldsEditor({
  ref,
  target,
  onTarget,
  scope,
  node,
  prompts,
  fields: keys,
  edited,
  onApply,
  readOnly = false,
  compact = false,
  folded = false,
  locks,
  onLock,
}: {
  ref?: Ref<PromptFieldsHandle> | undefined;
  // The field a tapped snippet lands in, marked on its row; opening or focusing a field picks it.
  target?: string | null | undefined;
  onTarget?: ((field: string) => void) | undefined;
  scope: NodeScope;
  node: string;
  prompts: Record<string, NodePrompt>;
  fields: readonly string[];
  // The fields this config sets over the optimizer's template; absent for a target prompt.
  edited?: readonly string[] | undefined;
  onApply: ((patch: DraftPatch) => void) | undefined;
  locks: Record<string, boolean>;
  onLock: ((field: string, locked: boolean) => void) | undefined;
  readOnly?: boolean;
  compact?: boolean | undefined;
  // Each field one line, its first, opening to its box.
  folded?: boolean | undefined;
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
  // Every library snippet, the account's banked edits among them, is read back out of the text.
  const libraryState = useRead(libraryRead());
  const library = libraryState.status === "ready" ? libraryState.data : null;
  const served = useMemo<KnownSnippet[]>(
    () =>
      library === null
        ? []
        : library.snippets.map((s) => ({ text: s.text, id: s.id, supersedes: s.supersedes })),
    [library],
  );
  // An edit is banked as a version of its snippet in the campaign or check-in it is written in.
  const { campaignId, checkin } = useWorkspace();
  const experiment = campaignId ?? checkin?.draftId ?? null;
  const bank = useCommand<"bank-snippet">("prompt-snippets", { revalidate: false, scope: experiment });
  const sent = useRef(new Set<string>());
  // Typed since the last bank, by field: framed until the served library holds it, and never in
  // another field.
  const [typed, setTyped] = useState<Record<string, readonly KnownSnippet[]>>({});
  const unbanked = (key: string): KnownSnippet[] =>
    (typed[key] ?? []).filter((e) => !served.some((s) => s.text === e.text));
  const knownIn = (key: string): KnownSnippet[] => [...served, ...unbanked(key)];
  const onEdit = (key: string, was: KnownSnippet, text: string) =>
    setTyped((prev) => ({
      ...prev,
      [key]: [
        ...(prev[key] ?? []).filter((e) => e.text !== was.text),
        { text, id: null, supersedes: was.id ?? was.supersedes },
      ],
    }));
  // Each distinct text once: a refused one stays framed for the session, its failure shown.
  const bankEdits = (next: Record<string, string>) => {
    if (experiment === null) return;
    const owed = keys.flatMap((key) =>
      unbanked(key).flatMap((e) =>
        e.supersedes !== null &&
        !sent.current.has(e.text) &&
        segmentPrompt(next[key] ?? "", knownIn(key)).some(
          (s) => s.kind === "snippet" && s.text === e.text,
        )
          ? [{ supersedes: e.supersedes, text: e.text }]
          : [],
      ),
    );
    if (owed.length === 0) return;
    void bank.run(
      "bank-snippet",
      async () => {
        for (const edit of owed) {
          sent.current.add(edit.text);
          await postCommand("bank-snippet", { ...edit, experiment });
        }
      },
      () => invalidateReads("library"),
    );
  };
  const dragged = useDraggedSnippet();
  const droppable = !readOnly && onApply != null && dragged !== null;

  const setField = (key: string, v: string) => setDraft((prev) => ({ ...prev, [key]: v }));

  // Never re-sends a sent value: each patch is a CommandRecord on the draft's ledger.
  const commit = (next: Record<string, string>) => {
    if (readOnly || !onApply) return;
    bankEdits(next);
    const nextFp = JSON.stringify(asStrings(keys, next));
    if (nextFp === sentFp) return;
    setSentFp(nextFp);
    onApply(promptFieldsPatch(scope, node, prompts, asStrings(keys, next)));
  };

  // A drop is an edit committed at once: the field takes the snippet after its own text.
  const land = (key: string, snippet: string) => {
    if (readOnly || !onApply) return;
    const next = { ...draft, [key]: withSnippet(draft[key] ?? "", snippet) };
    setDraft(next);
    commit(next);
  };
  useImperativeHandle(ref, () => ({ insert: land }));
  const drop = (key: string) => (e: DragEvent<HTMLElement>) => {
    if (!droppable) return;
    e.preventDefault();
    land(key, dragged.text);
    endSnippetDrag();
  };

  const fewShot = own?.shots.length ?? 0;
  const rows = keys.map((key) => ({ key, ...promptField(key) }));
  const shown = compact && readOnly ? rows.filter((f) => (draft[f.key] ?? "").trim()) : rows;

  return (
    <section className={cx("prompt-editor", compact && "is-compact", folded && "is-folded")}>
      <span className="prompt-editor-title">
        {readOnly ? "Prompt, as banked" : scope === "optimizer" ? "Optimizer prompt" : "Starting prompt"}
        {Object.keys(prompts).length > 1 ? <code className="opt-detail-id">{node}</code> : null}
      </span>
      <div className="prompt-editor-grid">
        {shown.map((f) => {
          const locked = locks[f.key];
          const suited = dragged?.suits.some((part) => part === f.key) === true;
          const text = draft[f.key] ?? "";
          const label = (
            <span className="prompt-field-label">
              {f.label}
              {target === f.key ? <span className="prompt-field-target">inserts here</span> : null}
              {edited?.includes(f.key) ? (
                <Term
                  className="prompt-field-edited"
                  content={`This config sets ${f.label.toLowerCase()} over the template, as nodes.${node}.config.${f.key}.`}
                >
                  edited
                </Term>
              ) : null}
              {onLock && locked !== undefined ? (
                <LockButton
                  locked={locked}
                  readOnly={readOnly}
                  onClick={() => onLock(f.key, !locked)}
                />
              ) : null}
            </span>
          );
          // One shape whatever the text holds, so typing never remounts the box under the caret.
          const box = (
            <div
              className={cx("prompt-field-blocks", droppable && (suited ? "is-suited" : "is-unsuited"))}
              onFocus={onTarget ? () => onTarget(f.key) : undefined}
              // Moving between the field's own boxes is no blur of the field.
              onBlur={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget)) commit(draft);
              }}
              onDragOver={(e) => {
                if (!droppable) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = "copy";
              }}
              onDrop={drop(f.key)}
            >
              <PromptSegments
                label={f.label}
                text={text}
                known={knownIn(f.key)}
                library={library}
                rows={compact ? 2 : (f.rows ?? 2)}
                placeholder={f.hint}
                structured={folded && STRUCTURED.test(text)}
                readOnly={readOnly}
                onText={(v) => setField(f.key, v)}
                onEdit={(was, v) => onEdit(f.key, was, v)}
              />
            </div>
          );
          if (folded) {
            const isTarget = target === f.key;
            return (
              <details
                key={f.key}
                className={cx("prompt-field-fold", isTarget && "is-target")}
                onToggle={(e) => {
                  if (e.currentTarget.open) onTarget?.(f.key);
                }}
              >
                <summary>
                  {label}
                  <span className={cx("prompt-field-first", !text.trim() && "is-empty")}>
                    {text.trim() ? text.trim().split("\n", 1)[0] : "empty"}
                  </span>
                </summary>
                {box}
              </details>
            );
          }
          return (
            // A div, not a label: the lock beside the name must not join the box's accessible name.
            <div key={f.key} className="prompt-field">
              {label}
              {box}
            </div>
          );
        })}
      </div>
      <FailureLine failure={bank.failure} lead="Banking the edit:" />
      {fewShot > 0 ? (
        <p className="prompt-editor-fewshot">
          + {fewShot} shot{fewShot === 1 ? "" : "s"} (kept as-is)
        </p>
      ) : null}
    </section>
  );
}
