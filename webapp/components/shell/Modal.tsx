"use client";

import { Button, Dialog } from "@/components/ui";
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

// The canonical confirm dialog. Actions render in order — rightmost is the primary.
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
