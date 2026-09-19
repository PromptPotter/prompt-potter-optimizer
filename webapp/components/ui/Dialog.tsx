"use client";

import { type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useDialogA11y } from "@/lib/hooks/useDialogA11y";
import s from "./Dialog.module.css";

// The accessible name: a `title` the dialog prints, or the id of a heading the host renders.
type Name = { title: string; labelledBy?: never } | { labelledBy: string; title?: never };

type Props = Name & {
  open: boolean;
  /** Absent = undismissable: Escape and the backdrop do nothing, and the host owns the exit. */
  onClose?: () => void;
  children: ReactNode;
  /** Footer actions (right-aligned). Rightmost is the primary by convention. */
  footer?: ReactNode;
  /** No card chrome — the host draws its own card, and `title` then only names it. */
  bare?: boolean;
};

export function Dialog({ open, title, labelledBy, onClose, children, footer, bare }: Props) {
  const cardRef = useDialogA11y(open, onClose);

  if (!open || typeof document === "undefined") return null;

  // mousedown (not click) on the backdrop avoids closing when a drag started
  // inside the card and released on the backdrop.
  return createPortal(
    <div className={s.backdrop} onMouseDown={onClose}>
      <div
        ref={cardRef}
        className={bare ? s.bare : s.card}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        aria-labelledby={labelledBy}
        tabIndex={-1}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {!bare && title !== undefined && <h2 className={s.title}>{title}</h2>}
        {children}
        {footer != null && <div className={s.actions}>{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
