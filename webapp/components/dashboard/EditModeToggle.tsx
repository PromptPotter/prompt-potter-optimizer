"use client";

interface Props {
  on: boolean;
  onToggle: (next: boolean) => void;
}

// Gates the risky actions on View Results — Fork-from-here in the
// ScoringInspector, and the cycle-level Stop button next to the picker.
// Off by default; explicitly reset on every page reload (no localStorage,
// no URL stickiness) so the operator never finds risky affordances
// silently visible.
export function EditModeToggle({ on, onToggle }: Props) {
  return (
    <button
      type="button"
      className={`edit-mode-toggle${on ? " on" : ""}`}
      onClick={() => onToggle(!on)}
      aria-pressed={on}
      title={on ? "Editing — risky actions exposed" : "Read-only view"}
    >
      {on ? "● Editing" : "Edit"}
    </button>
  );
}
