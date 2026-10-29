"use client";

import type { LibraryResponse } from "@/lib/api";
import { cx } from "@/lib/cx";
import {
  freeText,
  joinSegments,
  lineageRoot,
  segmentPrompt,
  withFreeText,
  withSnippetText,
  type KnownSnippet,
  type Segment,
} from "@/lib/derivations";

// The source a block names: its lineage's first snippet's work, or none for a text the library lacks.
function sourceOf(library: LibraryResponse | null, known: KnownSnippet): string | null {
  const id = known.id ?? known.supersedes;
  const root = library === null || id === null ? undefined : lineageRoot(library.snippets, id);
  if (root === undefined) return null;
  const source = library?.sources.find((x) => x.id === root.source);
  if (source === undefined) return root.source;
  return source.short_title;
}

// One prompt field: each snippet a framed block naming its source, a box to write in before, between
// and after them. Writing in a block keeps its frame, marked as a version of what it was edited from.
export function PromptSegments({
  label,
  text,
  known,
  library,
  rows,
  placeholder,
  structured,
  readOnly,
  onText,
  onEdit,
}: {
  label: string;
  text: string;
  known: readonly KnownSnippet[];
  library: LibraryResponse | null;
  rows: number;
  placeholder: string | undefined;
  structured: boolean;
  readOnly: boolean;
  onText: (text: string) => void;
  // A block's text changed: the snippet it stood for and what it now reads.
  onEdit: (was: KnownSnippet, text: string) => void;
}) {
  const segments = segmentPrompt(text, known);
  const holds = segments.length > 1;
  const write = (next: readonly Segment[]) => onText(joinSegments(next));
  // Dropping a block drops one of the two newlines that stood it on its own lines.
  const remove = (i: number) => {
    let left = joinSegments(segments.slice(0, i));
    let right = joinSegments(segments.slice(i + 1));
    if (left.endsWith("\n")) left = left.slice(0, -1);
    else if (right.startsWith("\n")) right = right.slice(1);
    onText(left + right);
  };

  return (
    <div className={cx("prompt-segments", holds && "has-snippets")} role="group" aria-label={label}>
      {segments.map((s, i) => {
        if (s.kind === "free") {
          const value = freeText(s);
          // Around a snippet an empty box stays one line; alone it is the whole field.
          return readOnly && holds && value === "" ? null : (
            <textarea
              key={`f${i}`}
              className={cx("prompt-field-input", holds && "prompt-seg-free", structured && "is-structured")}
              aria-label={holds ? `${label}, text` : label}
              rows={holds ? 1 : rows}
              value={value}
              placeholder={holds ? "…" : placeholder}
              readOnly={readOnly}
              disabled={readOnly}
              onChange={(e) =>
                write(segments.map((x, j) => (j === i ? withFreeText(s, e.target.value) : x)))
              }
            />
          );
        }
        const source = sourceOf(library, s.snippet);
        const edited = s.snippet.supersedes !== null;
        return (
          <div key={`s${i}`} className={cx("prompt-snippet", edited && "is-edited")}>
            <span className="prompt-snippet-tag">
              {edited ? <span className="prompt-snippet-edited">✎ from</span> : null}
              {source === null ? null : <span className="prompt-snippet-source">{source}</span>}
              {readOnly ? null : (
                <button
                  type="button"
                  className="prompt-snippet-drop"
                  aria-label="Remove this snippet"
                  onClick={() => remove(i)}
                >
                  ×
                </button>
              )}
            </span>
            <textarea
              className="prompt-field-input prompt-snippet-input"
              aria-label={source === null ? `${label}, snippet` : `${label}, snippet from ${source}`}
              rows={1}
              value={s.text}
              readOnly={readOnly}
              disabled={readOnly}
              onChange={(e) => {
                if (e.target.value.trim() === "") {
                  remove(i);
                  return;
                }
                onEdit(s.snippet, e.target.value);
                write(withSnippetText(segments, i, e.target.value));
              }}
            />
          </div>
        );
      })}
    </div>
  );
}
