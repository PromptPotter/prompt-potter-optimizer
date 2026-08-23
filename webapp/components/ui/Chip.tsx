import type { CSSProperties, ReactNode } from "react";
import { cx } from "@/lib/cx";
import s from "./Chip.module.css";

// A single pressable toggle — independently on/off, unlike a `SegmentedControl`
// segment (which is one of an exclusive set). Carries its own `aria-pressed`, so
// the state is never conveyed by colour alone.
//
// This is the old global `.fitness-chip`, which had leaked out of the fitness
// card into every surface that wanted a small toggle.
export function Chip({
  on,
  onClick,
  title,
  disabled,
  ariaLabel,
  icon,
  ink,
  children,
}: {
  on: boolean;
  onClick: () => void;
  title?: string;
  disabled?: boolean;
  // REQUIRED when `icon` is set: an icon-only control has no text to name it, so
  // this is the only accessible name it gets.
  ariaLabel?: string;
  // Square, icon-sized. The label then lives in `title` + `ariaLabel` only.
  icon?: boolean;
  // A colour this chip's lit state should wear instead of the accent — for a chip that
  // switches something DRAWN, so the control carries the same ink as the thing it turns
  // on and needs no legend entry of its own. Only the `joined` underline reads it; state
  // is still carried by `aria-pressed`, never by colour.
  ink?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className={cx(s.chip, icon && s.icon, on && s.on)}
      style={ink ? ({ "--ink": ink } as CSSProperties) : undefined}
      aria-pressed={on}
      aria-label={ariaLabel}
      title={title}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

// A cluster of related chips. `label` is the group's ACCESSIBLE name — the chips
// inside never restate it — and is drawn only when `showLabel` is set. In a
// toolbar it stays undrawn: a row of icons is dense precisely because it spends
// no width on words, and the tooltips carry the meaning.
//
// `joined` fuses them into one framed bar with no internal borders and an
// underline on whatever is on. Use it when the chips are facets of ONE concept
// (the metric axis: % / ∑ / θ) rather than unrelated switches — three separately
// outlined pill buttons say "three things", and they aren't.
export function ChipGroup({
  label,
  showLabel,
  joined,
  children,
}: {
  label: string;
  showLabel?: boolean;
  joined?: boolean;
  children: ReactNode;
}) {
  return (
    <span className={cx(s.group, joined && s.joined)} role="group" aria-label={label}>
      {showLabel && <span className={s.groupLabel}>{label}</span>}
      {children}
    </span>
  );
}
