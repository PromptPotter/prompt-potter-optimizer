"use client";

import { Button, Dialog } from "@/components/ui";

export function CleanupConfirmModal({
  stubCount,
  cleaning,
  error,
  onCancel,
  onConfirm,
}: {
  stubCount: number;
  cleaning: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog
      open
      title={`Clean up ${stubCount} empty stub${stubCount === 1 ? "" : "s"}?`}
      onClose={onCancel}
      footer={
        <>
          <Button onClick={onCancel} disabled={cleaning}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onConfirm} disabled={cleaning}>
            {cleaning ? "Cleaning…" : "Delete stubs"}
          </Button>
        </>
      }
    >
      <p className="family-tree-modal-body">
        Removes every fork / diag dir in this campaign that has{" "}
        <code>n_rounds = 0</code> and no descendants. Units that ran real work,
        the active unit, session roots, and units with children are skipped.
      </p>
      {error && <p className="family-tree-modal-error">{error}</p>}
    </Dialog>
  );
}
