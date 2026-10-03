"use client";

import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import { Popover } from "./Popover";
import s from "./Menu.module.css";

// The overflow menu behind a toolbar's "⋯", for controls that are real but rare.
export function Menu({
  renderTrigger,
  align = "right",
  children,
}: {
  renderTrigger: (args: { open: boolean; toggle: () => void }) => ReactNode;
  align?: "left" | "right";
  children: (args: { close: () => void }) => ReactNode;
}) {
  return (
    <Popover align={align} renderTrigger={renderTrigger}>
      {({ close }) => (
        <div className={s.menu} role="menu">
          {children({ close })}
        </div>
      )}
    </Popover>
  );
}

export function MenuItem({
  onClick,
  disabled,
  children,
}: {
  onClick: () => void;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className={s.item}
      disabled={disabled}
      // A menu opened inside a `<summary>` or clickable row must not fire that frame.
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onClick();
      }}
    >
      <span className={s.check} aria-hidden="true" />
      <span className={s.label}>{children}</span>
    </button>
  );
}

export function MenuCheck({
  on,
  onClick,
  disabled,
  title,
  children,
}: {
  on: boolean;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="menuitemcheckbox"
      aria-checked={on}
      className={cx(s.item, on && s.on)}
      disabled={disabled}
      title={title}
      onClick={onClick}
    >
      <span className={s.check} aria-hidden="true">{on ? "✓" : ""}</span>
      <span className={s.label}>{children}</span>
    </button>
  );
}

export function MenuRadioGroup<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: readonly { value: T; label: string; heading?: never }[] | readonly {
    value?: T;
    label?: string;
    heading?: string;
  }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className={s.group} role="group" aria-label={label}>
      <span className={s.groupLabel}>{label}</span>
      {options.map((o, i) =>
        o.heading ? (
          <span key={`h${i}`} className={s.heading}>{o.heading}</span>
        ) : (
          <button
            key={o.value}
            type="button"
            role="menuitemradio"
            aria-checked={value === o.value}
            className={cx(s.item, s.sub, value === o.value && s.on)}
            onClick={() => onChange(o.value as T)}
          >
            <span className={s.check} aria-hidden="true">{value === o.value ? "✓" : ""}</span>
            <span className={s.label}>{o.label}</span>
          </button>
        ),
      )}
    </div>
  );
}

export function MenuSep() {
  return <span className={s.sep} role="separator" />;
}
