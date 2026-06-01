"use client";

import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import s from "@/components/ui/Dialog.module.css";

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

// Confirm dialog: title + message + a row of action buttons. The canonical
// confirm surface, built on the Dialog + Button primitives (which carry the
// focus-trap / ESC / restore a11y). Actions render in order — rightmost is the
// primary by convention.
export function Modal({ open, title, message, actions, onClose }: Props) {
  return (
    <Dialog
      open={open}
      title={title}
      onClose={onClose}
      footer={actions.map((a) => (
        <Button key={a.label} variant={a.variant ?? "default"} onClick={a.onClick}>
          {a.label}
        </Button>
      ))}
    >
      <p className={s.message}>{message}</p>
    </Dialog>
  );
}
