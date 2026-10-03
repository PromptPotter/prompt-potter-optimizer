import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import s from "./SegmentedControl.module.css";

// Exclusive choice, exactly one segment on. A non-exclusive set is `Chip`.
export interface Segment<T extends string> {
  value: T;
  // An icon label REQUIRES `ariaLabel`.
  label: ReactNode;
  ariaLabel?: string;
  title?: string;
  disabled?: boolean;
  // A gauge drawn behind the label, independent of which segment is on.
  fill?: "full" | "part";
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  className,
  size = "sm",
}: {
  options: readonly Segment<T>[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
  className?: string;
  size?: "sm" | "lg";
}) {
  return (
    <span className={cx(s.group, size === "lg" && s.lg, className)} role="group" aria-label={ariaLabel}>
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            className={cx(s.segment, o.fill && s[o.fill], on && s.on)}
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
