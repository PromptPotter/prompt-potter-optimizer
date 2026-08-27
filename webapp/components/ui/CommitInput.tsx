"use client";
import { useState } from "react";

// A text input that commits on Enter or blur, never per keystroke.
//
// Behavioural rather than presentational, which is why it carries no stylesheet — the caller keeps
// its own class. What it owns is the discipline three surfaces hand-rolled separately (the metric
// expression, the scoring-mask criterion, a config cell in the compare table), each with its own draft
// state and its own reset-or-not decision.
//
// The rule it enforces is `webapp/CLAUDE.md` § Component conventions: every half-typed value here
// is a VALID but wrong request — a keystroke commit fires one fetch per character, 400s on every
// half-written formula, and blanks the card under the cursor still typing.
//
// The render-phase reset is the half the copies kept getting wrong: a committed value arriving from
// elsewhere (a channel re-pointed, a cell restored, a cycle bound) must replace the draft in the
// same render, because a `useEffect` reset paints one frame of the previous unit's text.
export function CommitInput({
  value,
  onCommit,
  ...rest
}: {
  value: string;
  onCommit: (value: string) => void;
} & Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  "value" | "onChange" | "onBlur" | "onKeyDown"
>) {
  const [draft, setDraft] = useState(value);
  // Two latches, because "the prop moved" and "the prop is stale relative to what I sent" are
  // different facts and one slot cannot tell them apart. `seen` triggers the reset; `sent` is what
  // was last handed UP. A caller that REJECTS a commit keeps its old `value` on screen deliberately
  // (`useFetch` `survive:"invalid"` — losing the form on a typo is the failure that rule prevents),
  // so a single latch either re-fires the rejected value on the blur after the Enter, or wipes what
  // the operator typed.
  const [seen, setSeen] = useState(value);
  const [sent, setSent] = useState(value);
  if (value !== seen) {
    setSeen(value);
    setSent(value);
    setDraft(value);
  }
  const commit = () => {
    if (draft === sent) return;
    setSent(draft);
    onCommit(draft);
  };
  return (
    <input
      {...rest}
      value={draft}
      spellCheck={false}
      autoComplete="off"
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        commit();
      }}
    />
  );
}
