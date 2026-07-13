"use client";

import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import { Popover } from "./Popover";
import s from "./Menu.module.css";

// The overflow menu behind a toolbar's "⋯" — where controls go when they're real
// but rare. A toolbar earns its density by spending width only on what you read
// constantly; everything else lives one click away, not on a second row.
//
// Built on `Popover`, so click-outside and Escape already work. The trigger is
// the caller's (usually a `Chip icon`) so it inherits the toolbar's own styling.
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

// A checkable row — the menu form of a toggle. `on` renders the check, so the
// state is legible without opening anything else.
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

// A plain action row (opens something, runs something).
export function MenuItem({
  onClick,
  title,
  children,
}: {
  onClick: () => void;
  title?: string;
  children: ReactNode;
}) {
  return (
    <button type="button" role="menuitem" className={s.item} title={title} onClick={onClick}>
      <span className={s.check} aria-hidden="true" />
      <span className={s.label}>{children}</span>
    </button>
  );
}

// A row that picks one of N — renders the current value on the right, and the
// options inline beneath when open. Keeps a submenu's affordance without a
// submenu's hover-intent problems.
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
