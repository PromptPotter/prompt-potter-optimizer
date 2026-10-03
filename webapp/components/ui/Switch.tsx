// The one switch. `locked` is a visibly unavailable control, never a dead one styled live
// (frontend-surface-contract.md § I3); styling is `.toggle` in app/styles/domains/chat.css.

import { cx } from "@/lib/cx";

export function Switch({
  checked,
  onChange,
  label,
  locked = false,
  // Pass one for a passing lock (command in flight), or a screen reader hears "coming soon".
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
