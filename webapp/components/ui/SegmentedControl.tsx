import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import s from "./SegmentedControl.module.css";

// Exclusive choice across 2–4 short options — one joined button group where
// exactly one segment is always on. Three surfaces hand-rolled this shape before
// it existed (the candidates view switch, the HIT/MISS sample filter, the
// pipeline-mode picker), each with its own class names and its own `aria`
// spelling; this is the one implementation they all read now.
//
// Exclusive by construction: `value` is the single source of truth, so the group
// cannot land in an empty or multi-selected state. For a NON-exclusive set of
// toggles (each independently on/off), reach for `Chip` instead.
export interface Segment<T extends string> {
  value: T;
  // Text, or an icon. When it's an icon, `ariaLabel` is REQUIRED — a glyph has
  // no accessible name of its own.
  label: ReactNode;
  ariaLabel?: string;
  title?: string;
  // An option the current state can't offer (e.g. a pipeline mode whose steps
  // this dataset has no research node for). Still rendered, so the choice stays
  // discoverable — just not selectable.
  disabled?: boolean;
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  className,
}: {
  options: readonly Segment<T>[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
  className?: string;
}) {
  return (
    <span className={cx(s.group, className)} role="group" aria-label={ariaLabel}>
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            className={cx(s.segment, on && s.on)}
            aria-pressed={on}
            aria-label={o.ariaLabel}
            title={o.title}
            disabled={o.disabled}
            onClick={() => onChange(o.value)}
          >
            {o.label}
          </button>
        );
      })}
    </span>
  );
}
