"use client";

// Confirm dialog for the campaign-wide empty-stub cleanup. Empty-row stubs
// accumulate because the fork-creation paths mint the cycle dir BEFORE the
// first round runs — an interrupt between dir-mint and first-round leaves a
// stub forever.
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
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Confirm stub cleanup"
      onClick={onCancel}
    >
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <h2 className="modal-title">
          Clean up {stubCount} empty stub{stubCount === 1 ? "" : "s"}?
        </h2>
        <p className="family-tree-modal-body">
          Removes every fork / sweep / diag dir in this campaign that has{" "}
          <code>n_rounds = 0</code> and no descendants. Units that ran real
          work, the active unit, session roots, and units with children are
          skipped.
        </p>
        {error && <p className="family-tree-modal-error">{error}</p>}
        <div className="modal-actions">
          <button type="button" onClick={onCancel} disabled={cleaning}>
            Cancel
          </button>
          <button
            type="button"
            className="modal-primary"
            onClick={onConfirm}
            disabled={cleaning}
          >
            {cleaning ? "Cleaning…" : "Delete stubs"}
          </button>
        </div>
      </div>
    </div>
  );
}
