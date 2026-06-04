"use client";
// Headless modal a11y — the behavior half of a dialog, with no visual chrome.
// While `open`, it: moves focus into the card, traps Tab within it, closes on
// ESC, and restores focus to the previously-focused element on close. The
// `Dialog` primitive uses this under its 480px card; bespoke-layout modals
// (account, auth, ingest) use it directly so they inherit the same a11y
// contract without being forced into Dialog's chrome.
//
// Attach the returned ref to the focus-trap container (the modal card). The
// caller still renders its own backdrop + close button and wires those to
// `onClose` — only keyboard + focus management lives here.

import { useEffect, useRef } from "react";

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';

export function useDialogA11y(open: boolean, onClose: () => void) {
  const cardRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement as HTMLElement | null;
    const card = cardRef.current;
    (card?.querySelector<HTMLElement>(FOCUSABLE) ?? card)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key !== "Tab" || !card) return;
      const items = Array.from(card.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      restoreRef.current?.focus?.();
    };
  }, [open, onClose]);

  return cardRef;
}
