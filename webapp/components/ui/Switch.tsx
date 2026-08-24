// Pill toggle switch — the one switch primitive. `locked` renders a
// disabled, clearly-unavailable control (a coming-soon feature) rather than
// a dead element styled like a live one (frontend-surface-contract.md § I3).
// Styling rides the existing `.toggle` / `.toggle.on` / `.toggle.locked`
// rules in app/styles/domains/chat.css.

import { cx } from "@/lib/cx";

export function Switch({
  checked,
  onChange,
  label,
  locked = false,
  // WHY it is locked, for the accessible name. Defaults to the common case; a
  // switch held for a passing reason (a command in flight, a run still warming)
  // passes its own, or a screen reader hears "coming soon" about a live feature.
  lockedNote = "coming soon",
}: {
  checked: boolean;
  onChange?: () => void;
  label: string;
  locked?: boolean;
  lockedNote?: string;
}) {
  return (
    <button
      type="button"
      className={cx("toggle", checked && "on", locked && "locked")}
      role="switch"
      aria-checked={checked}
      aria-label={locked ? `${label} (${lockedNote})` : label}
      aria-disabled={locked || undefined}
      disabled={locked}
      onClick={locked ? undefined : onChange}
    />
  );
}
