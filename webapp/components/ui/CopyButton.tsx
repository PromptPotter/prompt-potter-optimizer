"use client";

import { useState } from "react";
import { Button } from "./Button";
import s from "./CopyButton.module.css";

// Copies a text representation of `data` to the clipboard with a brief "Copied"
// flash — the fast path for dropping a box's contents into an AI instead of
// screenshotting. Pass an object (JSON-stringified, pretty) or a ready string;
// the payload mirrors the on-disk dashboard.json / round_NNNN.json surface.
// Whatever is on screen stays the selectable fallback if the clipboard is
// blocked (non-secure context, denied permission).
export function CopyButton({
  data,
  title = "Copy as JSON",
}: {
  data: unknown;
  title?: string;
}) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    let text: string;
    try {
      text = typeof data === "string" ? data : JSON.stringify(data, null, 2);
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
  return (
    <Button
      variant="ghost"
      className={s.copy}
      onClick={() => void copy()}
      aria-label={copied ? "Copied" : title}
      title={title}
    >
      {copied ? <CheckIcon /> : <CopyIcon />}
    </Button>
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
