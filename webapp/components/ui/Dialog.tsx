"use client";

import { type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useDialogA11y } from "@/lib/hooks/useDialogA11y";
import s from "./Dialog.module.css";

interface Props {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** Footer actions (right-aligned). Rightmost is the primary by convention. */
  footer?: ReactNode;
}

// Accessible modal dialog: portalled to <body>, ESC closes, backdrop click
// closes, focus moves in on open and is restored to the prior element on close,
// and Tab is trapped within the card (all via useDialogA11y). `title` is the
// accessible name. The visual chrome (480px card) lives here; bespoke-layout
// modals reuse the hook directly instead of this card.
export function Dialog({ open, title, onClose, children, footer }: Props) {
  const cardRef = useDialogA11y(open, onClose);

  if (!open || typeof document === "undefined") return null;

  // mousedown (not click) on the backdrop avoids closing when a drag started
  // inside the card and released on the backdrop.
  return createPortal(
    <div className={s.backdrop} onMouseDown={onClose}>
      <div
        ref={cardRef}
        className={s.card}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h2 className={s.title}>{title}</h2>
        {children}
        {footer != null && <div className={s.actions}>{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
