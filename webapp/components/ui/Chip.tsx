import type { CSSProperties, ReactNode } from "react";
import { cx } from "@/lib/cx";
import s from "./Chip.module.css";

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
  // REQUIRED when `icon` is set — the only accessible name an icon-only chip gets.
  ariaLabel?: string;
  icon?: boolean;
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

export function ChipGroup({
  label,
  joined,
  showLabel,
  children,
}: {
  label: string;
  joined?: boolean;
  showLabel?: boolean;
  children: ReactNode;
}) {
  return (
    <span className={cx(s.group, joined && s.joined)} role="group" aria-label={label}>
      {showLabel && (
        <span className={s.groupLabel} aria-hidden="true">
          {label}:
        </span>
      )}
      {children}
    </span>
  );
}
