"use client";

import { useState, type MouseEvent, type ReactNode } from "react";
import { cx } from "@/lib/cx";
import { Button } from "./Button";
import { Menu, MenuItem } from "./Menu";
import s from "./CopyButton.module.css";

export interface CopyChoice {
  key: string;
  label: string;
  data: unknown;
}

type Props = {
  title?: string;
  children?: ReactNode;
  disabled?: boolean;
} & (
  | { data: unknown; choices?: never }
  | { data?: never; choices: readonly CopyChoice[] }
);

// The one clipboard copy. A payload is an object (pretty JSON), a string, or a thunk for either;
// never host one inside a LABEL (webapp/CLAUDE.md § Component conventions).
export function CopyButton({ data, title = "Copy as JSON", choices, children, disabled }: Props) {
  const [copied, setCopied] = useState(false);
  // Every host frames this in something clickable (a `<summary>`, a selectable row); swallowed here
  // so one click does not also fire the frame. `Popover` dismisses on mousedown, so this is safe.
  const swallow = (e: MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };
  const copy = async (payload: unknown) => {
    let text: string;
    try {
      // A thunk resolves at click, not render: a payload stamping "captured: …" must mean the ask.
      const value = typeof payload === "function" ? (payload as () => unknown)() : payload;
      text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    } catch {
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard unavailable — the rendered content stays selectable.
    }
  };

  const face = children ? (copied ? "Copied" : children) : copied ? <CheckIcon /> : <CopyIcon />;

  if (choices && choices.length === 0) return null;

  const only = choices?.length === 1 ? choices[0] : null;

  if (!choices || only) {
    const name = only ? `${title} — ${only.label}` : title;
    return (
      <Button
        variant="ghost"
        className={cx(s.copy, !!children && s.text)}
        disabled={disabled}
        onClick={(e) => {
          swallow(e);
          void copy(only ? only.data : data);
        }}
        aria-label={copied ? "Copied" : name}
        title={name}
      >
        {face}
      </Button>
    );
  }

  return (
    <Menu
      align="right"
      renderTrigger={({ open, toggle }) => (
        <Button
          variant="ghost"
          className={cx(s.copy, !!children && s.text)}
          disabled={disabled}
          onClick={(e) => {
            swallow(e);
            toggle();
          }}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label={copied ? "Copied" : title}
          title={title}
        >
          {face}
        </Button>
      )}
    >
      {({ close }) => (
        <>
          {choices.map((c) => (
            <MenuItem
              key={c.key}
              onClick={() => {
                close();
                void copy(c.data);
              }}
            >
              {c.label}
            </MenuItem>
          ))}
        </>
      )}
    </Menu>
  );
}

function CopyIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}
