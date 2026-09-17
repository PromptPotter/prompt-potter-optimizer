"use client";
import {
  useState,
  type ChangeEvent,
  type InputHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

// A text control that commits on Enter or blur — on blur alone once `rows` makes it multi-line —
// and never per keystroke.
//
// Behavioural rather than presentational, which is why it carries no stylesheet — the caller keeps
// its own class. What it owns is the discipline three surfaces hand-rolled separately (the metric
// expression, the scoring-mask criterion, a config cell in the compare table), each with its own draft
// state and its own reset-or-not decision.
//
// The rule it enforces is `webapp/CLAUDE.md` § Component conventions: every half-typed value here
// is a VALID but wrong request — a keystroke commit fires one fetch per character, 400s on every
// half-written formula, and blanks the card under the cursor still typing.

type Own = {
  value: string;
  onCommit: (value: string) => void;
  // A draft this returns false for is NOT committed and stays on screen — a half-typed JSON value
  // must not reach a caller as a string, and losing what was typed is the failure `sent` exists to
  // prevent. Named `validate`, not `accept`: that one is a DOM attribute and would merge silently.
  validate?: (draft: string) => boolean;
};

type Owned = "value" | "onChange" | "onBlur" | "onKeyDown";

// `rows` picks the ELEMENT, so each arm carries that element's own attributes. A structured value
// (a JSON schema, a layout) must be SEEN to be edited, and it cannot also be given `type` or any
// other input-only attribute — the signature is what refuses that, so nothing is stripped at render.
type OneLine = Own & Omit<InputHTMLAttributes<HTMLInputElement>, Owned>;
type MultiLine = Own & { rows: number } & Omit<
    TextareaHTMLAttributes<HTMLTextAreaElement>,
    Owned | "rows"
  >;

// Two latches, because "the prop moved" and "the prop is stale relative to what I sent" are
// different facts and one slot cannot tell them apart. `seen` triggers the reset; `sent` is what
// was last handed UP. A caller that REJECTS a commit keeps its old `value` on screen deliberately
// (`useRead` `survive:"invalid"` — losing the form on a typo is the failure that rule prevents),
// so a single latch either re-fires the rejected value on the blur after the Enter, or wipes what
// the operator typed.
//
// The reset runs during RENDER: a committed value arriving from elsewhere (a channel re-pointed, a
// cell restored, a cycle bound) must replace the draft in the same render, because a `useEffect`
// reset paints one frame of the previous unit's text.
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

// Enter is a newline in a multi-line value, so this arm commits on blur alone: a schema typed
// across four lines cannot be a control whose first Return sends it.
function Area({ value, onCommit, validate, rows, ...rest }: MultiLine) {
  const { field } = useCommitted(value, onCommit, validate);
  return <textarea {...rest} {...field} rows={rows} />;
}

export function CommitInput(props: OneLine | MultiLine) {
  return "rows" in props ? <Area {...props} /> : <Line {...props} />;
}
