"use client";
import { useState } from "react";
import { postChangeSpendBudget, IngestApiError } from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { fmtUsd, fmtTokens } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";
import { Modal } from "@/components/shell/Modal";

interface Props {
  // The two standing ceilings as the live dashboard reports them
  // (`run_limits.spend_budget_usd` / `run_limits.token_budget`); `null` =
  // disarmed. Used to prefill the inputs + show the standing caps.
  currentBudgetUsd: number | null;
  currentBudgetTokens: number | null;
  usedUsd: number | null;
  usedTokens: number;
}

// Fulfils the job-bar "Adjust spend / finishing criteria" affordance with the
// shipped `change-spend-budget` command — now two ceilings (USD + tokens),
// whichever trips first halts. Write-only: it reads the standing caps from the
// dashboard poll (no new endpoint) and POSTs new caps. The runner's BudgetGate
// re-reads `.runtime/spend_cap.json` each clean round, so the change takes at the
// next round boundary. Setting a ceiling to `0` halts after the current round —
// confirmed first, since that's effectively a stop.
export function SpendBudgetControl({
  currentBudgetUsd,
  currentBudgetTokens,
  usedUsd,
  usedTokens,
}: Props) {
  const { campaignId, cycleId } = useWorkspace();
  const [usdDraft, setUsdDraft] = useState<string>(
    currentBudgetUsd != null ? String(currentBudgetUsd) : "",
  );
  const [tokDraft, setTokDraft] = useState<string>(
    currentBudgetTokens != null ? String(currentBudgetTokens) : "",
  );
  // Re-seed the edit buffers whenever the committed cap changes — render-phase
  // guarded reset (webapp/CLAUDE.md "State reset on prop change"), the same recipe
  // as useAppliableField. Without it the buffers keep their first-mount value: this
  // control lives in the RemoteControl's persistent (un-keyed) panel, so a soft unit
  // switch never remounts it, and `apply()` (posting to the now-updated workspace ids)
  // would write the prior cycle's cap onto the newly-viewed one.
  const [prevUsd, setPrevUsd] = useState(currentBudgetUsd);
  if (currentBudgetUsd !== prevUsd) {
    setPrevUsd(currentBudgetUsd);
    setUsdDraft(currentBudgetUsd != null ? String(currentBudgetUsd) : "");
  }
  const [prevTok, setPrevTok] = useState(currentBudgetTokens);
  if (currentBudgetTokens !== prevTok) {
    setPrevTok(currentBudgetTokens);
    setTokDraft(currentBudgetTokens != null ? String(currentBudgetTokens) : "");
  }
  const [pending, setPending] = useState(false);
  const [confirmingHalt, setConfirmingHalt] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const disabled = !campaignId || !cycleId;

  const parsedUsd = Number.parseFloat(usdDraft);
  const usdValid = usdDraft.trim() !== "" && Number.isFinite(parsedUsd) && parsedUsd >= 0;
  const usdChanged = usdValid && parsedUsd !== currentBudgetUsd;

  const parsedTok = Number.parseInt(tokDraft, 10);
  const tokValid =
    tokDraft.trim() !== "" && Number.isInteger(parsedTok) && parsedTok >= 0;
  const tokChanged = tokValid && parsedTok !== currentBudgetTokens;

  // Send only the ceilings the operator actually changed; the applier merges,
  // so an untouched ceiling is left exactly as it was on disk.
  const nextUsd = usdChanged ? parsedUsd : null;
  const nextTok = tokChanged ? parsedTok : null;
  const hasChange = nextUsd != null || nextTok != null;
  const isHalt = nextUsd === 0 || nextTok === 0;

  const apply = async () => {
    if (!campaignId || !cycleId || !hasChange) return;
    setConfirmingHalt(false);
    setPending(true);
    setErr(null);
    setNote(null);
    try {
      await postChangeSpendBudget(campaignId, cycleId, {
        maxUsd: nextUsd,
        maxTokens: nextTok,
      });
      bumpRevalidation();
      // The note quotes NO number, deliberately. `quota.py::clamp_budget_change` silently mins
      // the request against the account's remaining allowance, so composing this text from the
      // draft told a spent free-tier account "Cap set to 8.0M tok" while 0 was written. The two
      // rows above already show the ARMED ceiling as the server reports it, so the honest report
      // is to point at them rather than to restate — or re-derive — what landed.
      setNote(
        isHalt
          ? "Applied — halting after this round."
          : "Applied — the armed cap is shown above; it takes at the next round.",
      );
    } catch (e) {
      setErr(IngestApiError.toOperatorMessage(e));
    } finally {
      setPending(false);
    }
  };

  const onSet = () => {
    if (!hasChange) return;
    if (isHalt) {
      setConfirmingHalt(true);
      return;
    }
    void apply();
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
          value={usdDraft}
          disabled={disabled || pending}
          placeholder={currentBudgetUsd != null ? undefined : "no cap"}
          onChange={(e) => {
            setUsdDraft(e.target.value);
            setNote(null);
            setErr(null);
          }}
          aria-label="New spend cap in USD"
        />
      </div>
      <div className="row">
        <span className="lbl">Token cap</span>
        <span className="val">
          {currentBudgetTokens != null ? fmtTokens(currentBudgetTokens) : "Uncapped"}
          <span className="spend-control-used"> · {fmtTokens(usedTokens)} used</span>
        </span>
      </div>
      <div className="spend-control-edit">
        <span className="spend-control-prefix">#</span>
        <input
          type="number"
          min={0}
          step={1000}
          inputMode="numeric"
          value={tokDraft}
          disabled={disabled || pending}
          placeholder={currentBudgetTokens != null ? undefined : "no cap"}
          onChange={(e) => {
            setTokDraft(e.target.value);
            setNote(null);
            setErr(null);
          }}
          aria-label="New token cap"
        />
        <button
          type="button"
          className="spend-control-set"
          disabled={disabled || pending || !hasChange}
          onClick={onSet}
        >
          {pending ? "Setting…" : "Set caps"}
        </button>
      </div>
      {/* Two ceilings can stop a run and only ONE of them is editable here. Naming the other
          and where it lives beats inferring which one bound from a raise that did not move:
          that inference is a derivation the browser has no authority to make, and the account
          pane already serves the lifetime pair. */}
      <small className="spend-control-hint">
        {isHalt
          ? "Halts the run after the current round."
          : "Re-read each round — raise to release, set a cap to 0 to halt. Whichever trips first wins. A raise is also clamped by your account allowance (Account → Security)."}
      </small>
      {note ? <small className="spend-control-note">{note}</small> : null}
      {err ? <small className="new-campaign-error">{err}</small> : null}
      <Modal
        open={confirmingHalt}
        title="Halt this run?"
        message={`Setting a cap to 0 stops ${cycleId ?? "this unit"} after the current round completes. Measurements so far are preserved; you can resume later by raising the cap and re-running.`}
        actions={[
          { label: "Cancel", onClick: () => setConfirmingHalt(false) },
          { label: "Set 0 & halt", variant: "danger", onClick: () => void apply() },
        ]}
        onClose={() => setConfirmingHalt(false)}
      />
    </div>
  );
}
