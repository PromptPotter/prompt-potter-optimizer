"use client";

import { useState, type MouseEvent, type ReactNode } from "react";
import { failureKind, operatorMessage } from "@/lib/api";
import { cx } from "@/lib/cx";
import { reportIncident } from "@/lib/diagnostics";
import { Button } from "./Button";
import { Menu, MenuItem } from "./Menu";
import s from "./CopyButton.module.css";

interface CopyChoice {
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

export function CopyButton({ data, title = "Copy as JSON", choices, children, disabled }: Props) {
  const [copied, setCopied] = useState(false);
  // The sentence a failed copy leaves on the control until it lapses; null while none stands.
  const [failure, setFailure] = useState<string | null>(null);
  // Hosts frame this in something clickable (a `<summary>`, a row); one click must not also fire the frame.
  const swallow = (e: MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };
  const fail = (message: string) => {
    setFailure(message);
    window.setTimeout(() => setFailure(null), 5000);
  };
  const copy = async (payload: unknown) => {
    let text: string;
    try {
      // A thunk resolves at click, not render: a payload stamping "captured: …" must mean the ask.
      // It may be async — a served serialization is fetched at the click that asks for it.
      const value = typeof payload === "function" ? await (payload as () => unknown)() : payload;
      text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    } catch (e) {
      reportIncident(e, { surface: "copy" });
      fail(operatorMessage(e, failureKind(e)));
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      fail("The clipboard refused the copy.");
    }
  };

  const face = failure ? (
    children ? (
      "Copy failed"
    ) : (
      <FailIcon />
    )
  ) : children ? (
    copied ? (
      "Copied"
    ) : (
      children
    )
  ) : copied ? (
    <CheckIcon />
  ) : (
    <CopyIcon />
  );
  const stateClass = failure ? s.failed : undefined;

  if (choices && choices.length === 0) return null;

  const only = choices?.length === 1 ? choices[0] : null;

  if (!choices || only) {
    const name = failure ?? (only ? `${title} — ${only.label}` : title);
    return (
      <Button
        variant="ghost"
        className={cx(s.copy, !!children && s.text, stateClass)}
        disabled={disabled}
        onClick={(e) => {
          swallow(e);
          void copy(only ? only.data : data);
        }}
        aria-label={copied ? "Copied" : name}
        title={name}
        role={failure ? "alert" : undefined}
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
          className={cx(s.copy, !!children && s.text, stateClass)}
          disabled={disabled}
          onClick={(e) => {
            swallow(e);
            toggle();
          }}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label={copied ? "Copied" : (failure ?? title)}
          title={failure ?? title}
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

function FailIcon() {
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
      <path d="M18 6 6 18M6 6l12 12" />
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
