"use client";

import { useState, type MouseEvent, type ReactNode } from "react";
import { cx } from "@/lib/cx";
import { Button } from "./Button";
import { Menu, MenuItem } from "./Menu";
import s from "./CopyButton.module.css";

// One reading a panel can hand over. A panel offering several is offering several
// ANSWERS to "what is this thing" — the spec it runs, the spec plus what it scored,
// that plus every row — so the choice is named rather than guessed at.
export interface CopyChoice {
  key: string;
  label: string;
  // An object (pretty JSON), a ready string, or a thunk for either — see `data` below.
  data: unknown;
}

// Either one payload or a menu of them — never both, and never neither.
type Props = {
  title?: string;
  // Text face instead of the glyph, for a row where a bare icon reads as decoration.
  children?: ReactNode;
  disabled?: boolean;
} & (
  | { data: unknown; choices?: never }
  | { data?: never; choices: readonly CopyChoice[] }
);

// Copies a text representation to the clipboard with a brief "Copied" flash — the
// fast path for dropping a box's contents into an AI instead of screenshotting.
// Pass an object (JSON-stringified, pretty), a ready string, or a THUNK returning
// either — the thunk for anything whose value depends on when it was taken, or that
// is dear enough to build that every render should not. The payload mirrors the
// on-disk dashboard.json / round_NNNN.json surface. Whatever is on screen stays the
// selectable fallback if the clipboard is blocked (non-secure context, denied
// permission).
//
// With `choices` it becomes a menu of payloads, on `Menu` → `Popover` so
// click-outside and Escape are the ones every other panel already has. The flash
// lands on the trigger either way: the menu is gone by the time it fires.
export function CopyButton({ data, title = "Copy as JSON", choices, children, disabled }: Props) {
  const [copied, setCopied] = useState(false);
  // Every host frames this button with something else that is itself clickable — a `<summary>`
  // that toggles, a row that selects. Left to bubble, one click both copies and fires the frame,
  // so copying a searchpoint out of a disclosure SHUT the disclosure it copied from. Swallowed
  // here rather than at each host: the button has no default action of its own to lose, and
  // `Popover` dismisses on mousedown, so click-outside still works.
  const swallow = (e: MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };
  const copy = async (payload: unknown) => {
    let text: string;
    try {
      // A thunk is resolved HERE, not at render: a payload that stamps the moment it was taken
      // ("captured: …") would otherwise report when the panel drew, not when the operator asked.
      const value = typeof payload === "function" ? (payload as () => unknown)() : payload;
      text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    } catch {
      return; // Unserializable payload — nothing to copy.
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

  // Nothing to hand over — the panel is still loading, or holds no reading yet. Rendering the
  // trigger anyway opens an empty menu, which is a control that looks operable and is not.
  if (choices && choices.length === 0) return null;

  // A one-option group is not a choice (`searchPoint.ts::observeOptions`, `NodeDetail`'s observe
  // toggle): a menu holding one row costs a click to say what the plain button already does.
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

// The conventional copy glyph — two overlapping sheets, monochrome via
// `currentColor` so it tints with the box's text/accent, not the emoji palette.
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
