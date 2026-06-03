"use client";
import { useState } from "react";
import { postChangeSpendBudget, IngestApiError } from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { fmtUsd } from "@/lib/format";
import { Modal } from "@/components/shell/Modal";

interface Props {
  campaignId: string | null;
  cycleId: string | null;
  // Current cap as the live dashboard reports it (`dash.spend.budget_usd`);
  // `null` = uncapped. Used to prefill the input + show the standing cap.
  currentBudgetUsd: number | null;
  usedUsd: number | null;
}

// Fulfils the job-bar "Adjust spend / finishing criteria" affordance with the
// shipped `change-spend-budget` command. Write-only: it reads the standing cap
// from the dashboard poll (no new endpoint) and POSTs a new cap. The runner's
// `spend_cap_probe` re-reads `.runtime/spend_cap.json` each clean round, so the
// change takes at the next round boundary. Setting `0` halts after the current
// round — confirmed first, since that's effectively a stop.
export function SpendBudgetControl({ campaignId, cycleId, currentBudgetUsd, usedUsd }: Props) {
  const [draft, setDraft] = useState<string>(
    currentBudgetUsd != null ? String(currentBudgetUsd) : "",
  );
  const [pending, setPending] = useState(false);
  const [confirmingHalt, setConfirmingHalt] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const disabled = !campaignId || !cycleId;
  const parsed = Number.parseFloat(draft);
  const valid = draft.trim() !== "" && Number.isFinite(parsed) && parsed >= 0;
  const isHalt = valid && parsed === 0;
  const unchanged = valid && currentBudgetUsd != null && parsed === currentBudgetUsd;

  const apply = async (maxUsd: number) => {
    if (!campaignId || !cycleId) return;
    setConfirmingHalt(false);
    setPending(true);
    setErr(null);
    setNote(null);
    try {
      await postChangeSpendBudget(campaignId, cycleId, maxUsd);
      bumpRevalidation();
      setNote(
        maxUsd === 0
          ? "Cap set to $0 — halting after this round."
          : `Cap set to ${fmtUsd(maxUsd)} — takes at the next round.`,
      );
    } catch (e) {
      setErr(IngestApiError.toOperatorMessage(e));
    } finally {
      setPending(false);
    }
  };

  const onSet = () => {
    if (!valid) return;
    if (isHalt) {
      setConfirmingHalt(true);
      return;
    }
    void apply(parsed);
  };

  return (
    <div className="spend-control">
      <div className="row">
        <span className="lbl">Spend cap</span>
        <span className="val">
          {currentBudgetUsd != null ? fmtUsd(currentBudgetUsd) : "Uncapped"}
          {usedUsd != null ? (
            <span className="spend-control-used"> · {fmtUsd(usedUsd)} used</span>
          ) : null}
        </span>
      </div>
      <div className="spend-control-edit">
        <span className="spend-control-prefix">$</span>
        <input
          type="number"
          min={0}
          step={0.5}
          inputMode="decimal"
          value={draft}
          disabled={disabled || pending}
          placeholder={currentBudgetUsd != null ? undefined : "no cap"}
          onChange={(e) => {
            setDraft(e.target.value);
            setNote(null);
            setErr(null);
          }}
          aria-label="New spend cap in USD"
        />
        <button
          type="button"
          className="spend-control-set"
          disabled={disabled || pending || !valid || unchanged}
          onClick={onSet}
        >
          {pending ? "Setting…" : "Set cap"}
        </button>
      </div>
      <small className="spend-control-hint">
        {isHalt
          ? "Halts the run after the current round."
          : "Re-read each round — raise to release, set $0 to halt."}
      </small>
      {note ? <small className="spend-control-note">{note}</small> : null}
      {err ? <small className="new-campaign-error">{err}</small> : null}
      <Modal
        open={confirmingHalt}
        title="Halt this run?"
        message={`Setting the cap to $0 stops ${cycleId ?? "this unit"} after the current round completes. Measurements so far are preserved; you can resume later by raising the cap and re-running.`}
        actions={[
          { label: "Cancel", onClick: () => setConfirmingHalt(false) },
          { label: "Set $0 & halt", variant: "danger", onClick: () => void apply(0) },
        ]}
        onClose={() => setConfirmingHalt(false)}
      />
    </div>
  );
}
