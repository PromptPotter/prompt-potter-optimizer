import { type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useDialogA11y } from "@/lib/hooks/useDialogA11y";
import { Button } from "./Button";
import { IconClose } from "./icons";
import s from "./Dialog.module.css";

type Props = {
  open: boolean;
  title: string;
  // Absent = undismissable: Escape and the backdrop do nothing.
  onClose?: () => void;
  children: ReactNode;
  footer?: ReactNode;
};

export function Dialog({ open, title, onClose, children, footer }: Props) {
  const cardRef = useDialogA11y(open, onClose);

  if (!open || typeof document === "undefined") return null;

  // mousedown, not click: a drag started inside the card and released outside must not close it.
  return createPortal(
    <div
      className={s.backdrop}
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose?.();
      }}
    >
      <div
        ref={cardRef}
        className={s.card}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
        <div className={s.head}>
          <h2 className={s.title}>{title}</h2>
          {/* A dismissable dialog shows one way out: its footer's own, or this. */}
          {onClose && footer == null && (
            <Button variant="ghost" aria-label="Close" onClick={onClose}>
              <IconClose />
            </Button>
          )}
        </div>
        {children}
        {footer != null && <div className={s.actions}>{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
