"use client";
import { useEffect } from "react";

export interface ModalAction {
  label: string;
  variant?: "primary" | "danger" | "default";
  onClick: () => void;
}

interface Props {
  open: boolean;
  title: string;
  message: string;
  actions: ModalAction[];
  onClose: () => void;
}

// Plain-React modal — no portal, sits at the end of <main> via the
// consumer's own render. Escape closes; backdrop click closes. Multiple
// action buttons render in the order given (rightmost is the primary
// affordance in our convention).
export function Modal({ open, title, message, actions, onClose }: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="modal-title" className="modal-title">{title}</h3>
        <p className="modal-message">{message}</p>
        <div className="modal-actions">
          {actions.map((a) => (
            <button
              key={a.label}
              type="button"
              className={`modal-btn modal-btn-${a.variant ?? "default"}`}
              onClick={a.onClick}
            >
              {a.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
