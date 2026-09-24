"use client";
import { useState } from "react";
import { postChangeSpendBudget } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import { fmtUsd, fmtTokens } from "@/lib/format";
import { parseCap } from "@/lib/run-limits";
import { useWorkspace } from "@/lib/workspace";
import { Modal } from "@/components/shell/Modal";

interface Props {
  // `null` = disarmed.
  currentBudgetUsd: number | null;
  currentBudgetTokens: number | null;
  usedUsd: number | null;
  usedTokens: number;
}

// Arms both ceilings (USD, tokens) via `change-spend-budget`; BudgetGate re-reads them at the next
// round boundary. A `0` ceiling halts after the current round, so it is confirmed first.
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
  // This panel is never remounted on a unit switch, so re-seed here or `apply()` writes the prior
  // cycle's cap onto the new one.
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
  // Scoped to the cycle: a refusal from the previous unit must not sit under the new one's caps.
  const cmd = useCommand<"change-spend-budget">("spend-budget", { scope: cycleId });
  const [confirmingHalt, setConfirmingHalt] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const pending = cmd.pending !== null;

  const disabled = !campaignId || !cycleId;

  // Only changed caps are sent; the applier merges, so an untouched cap stays as it is on disk.
  const usd = parseCap(usdDraft);
  const tok = parseCap(tokDraft, { int: true });
  const nextUsd = usd !== currentBudgetUsd ? usd : null;
  const nextTok = tok !== currentBudgetTokens ? tok : null;
  const hasChange = nextUsd != null || nextTok != null;
  const isHalt = nextUsd === 0 || nextTok === 0;

  const apply = async () => {
    if (!campaignId || !cycleId || !hasChange) return;
    setConfirmingHalt(false);
    setNote(null);
    const r = await cmd.run("change-spend-budget", () =>
      postChangeSpendBudget(campaignId, cycleId, { maxUsd: nextUsd, maxTokens: nextTok }),
    );
    if (!r.ok) return;
    // Quotes NO number: `quota.py::clamp_budget_change` silently mins the request against the
    // remaining allowance; the rows above show what the server armed.
    setNote(
      isHalt
        ? "Applied — halting after this round."
        : "Applied — the armed cap is shown above; it takes at the next round.",
    );
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
            cmd.clear();
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
            cmd.clear();
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
      {/* Name the other ceiling rather than infer which one bound — the browser has no authority
          to make that derivation. */}
      <small className="spend-control-hint">
        {isHalt
          ? "Halts the run after the current round."
          : "Re-read each round — raise to release, set a cap to 0 to halt. Whichever trips first wins. A raise is also clamped by your account allowance (Account → Security)."}
      </small>
      {note ? <small className="spend-control-note">{note}</small> : null}
      {cmd.failure ? (
        <small className="new-campaign-error">{cmd.failure.message}</small>
      ) : null}
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
