"use client";
import {
  useState,
  type ChangeEvent,
  type InputHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

type Own = {
  value: string;
  onCommit: (value: string) => void;
  // False keeps the draft on screen uncommitted. Not `accept`: a DOM attribute would merge silently.
  validate?: (draft: string) => boolean;
};

type Owned = "value" | "onChange" | "onBlur" | "onKeyDown";

type OneLine = Own & Omit<InputHTMLAttributes<HTMLInputElement>, Owned>;
type MultiLine = Own & { rows: number } & Omit<
    TextareaHTMLAttributes<HTMLTextAreaElement>,
    Owned | "rows"
  >;

// Two latches: a caller that rejects a commit keeps its old `value`, so one would re-fire it on the next blur.
function useCommitted(
  value: string,
  onCommit: (value: string) => void,
  validate: ((draft: string) => boolean) | undefined,
) {
  const [draft, setDraft] = useState(value);
  const [seen, setSeen] = useState(value);
  const [sent, setSent] = useState(value);
  if (value !== seen) {
    setSeen(value);
    setSent(value);
    setDraft(value);
  }
  const commit = () => {
    if (draft === sent) return;
    if (validate && !validate(draft)) return;
    setSent(draft);
    onCommit(draft);
  };
  return {
    commit,
    field: {
      value: draft,
      spellCheck: false,
      autoComplete: "off" as const,
      onBlur: commit,
      onChange: (e: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setDraft(e.target.value),
    },
  };
}

function Line({ value, onCommit, validate, ...rest }: OneLine) {
  const { commit, field } = useCommitted(value, onCommit, validate);
  return (
    <input
      {...rest}
      {...field}
      onKeyDown={(e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        commit();
      }}
    />
  );
}

function Area({ value, onCommit, validate, rows, ...rest }: MultiLine) {
  const { field } = useCommitted(value, onCommit, validate);
  return <textarea {...rest} {...field} rows={rows} />;
}

export function CommitInput(props: OneLine | MultiLine) {
  return "rows" in props ? <Area {...props} /> : <Line {...props} />;
}
