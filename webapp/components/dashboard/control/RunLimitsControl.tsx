"use client";
import { useState } from "react";
import { postChangeRunLimits } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import { fmtUsd, fmtTokens } from "@/lib/format";
import { parseCap } from "@/lib/run-limits";
import { useWorkspace } from "@/lib/workspace";
import { Modal } from "@/components/shell/Modal";
import { Button } from "@/components/ui";

interface Props {
  // `null` = disarmed; for rounds, the spend caps alone govern.
  currentBudgetUsd: number | null;
  currentBudgetTokens: number | null;
  currentMaxRounds: number | null;
  usedUsd: number | null;
  usedTokens: number;
}

// Arms the run's caps (USD, tokens, rounds) via `change-run-limits`; the loop re-reads them at the
// next round boundary. A `0` cap halts after the current round, so it is confirmed first.
export function RunLimitsControl({
  currentBudgetUsd,
  currentBudgetTokens,
  currentMaxRounds,
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
  const [roundsDraft, setRoundsDraft] = useState<string>(
    currentMaxRounds != null ? String(currentMaxRounds) : "",
  );
  const [prevRounds, setPrevRounds] = useState(currentMaxRounds);
  if (currentMaxRounds !== prevRounds) {
    setPrevRounds(currentMaxRounds);
    setRoundsDraft(currentMaxRounds != null ? String(currentMaxRounds) : "");
  }
  // Scoped to the cycle: a refusal from the previous unit must not sit under the new one's caps.
  const cmd = useCommand<"change-run-limits">("run-limits", { scope: cycleId });
  const [confirmingHalt, setConfirmingHalt] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const pending = cmd.pending !== null;

  const disabled = !campaignId || !cycleId;

  // Only changed caps are sent; the applier merges, so an untouched cap stays as it is on disk.
  const usd = parseCap(usdDraft);
  const tok = parseCap(tokDraft, { int: true });
  const rounds = parseCap(roundsDraft, { int: true });
  const nextUsd = usd !== currentBudgetUsd ? usd : null;
  const nextTok = tok !== currentBudgetTokens ? tok : null;
  const nextRounds = rounds !== currentMaxRounds ? rounds : null;
  const hasChange = nextUsd != null || nextTok != null || nextRounds != null;
  const isHalt = nextUsd === 0 || nextTok === 0 || nextRounds === 0;

  const apply = async () => {
    if (!campaignId || !cycleId || !hasChange) return;
    setConfirmingHalt(false);
    setNote(null);
    // A null `maxRounds` would LIFT the cap, so an unchanged one is left out rather than sent.
    const r = await cmd.run("change-run-limits", () =>
      postChangeRunLimits(campaignId, cycleId, {
        maxUsd: nextUsd,
        maxTokens: nextTok,
        ...(nextRounds != null ? { maxRounds: nextRounds } : {}),
      }),
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

  const liftRounds = async () => {
    if (!campaignId || !cycleId) return;
    setNote(null);
    const r = await cmd.run("change-run-limits", () =>
      postChangeRunLimits(campaignId, cycleId, { maxRounds: null }),
    );
    if (!r.ok) return;
    setNote("Applied — no round cap from the next round; the spend caps govern.");
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
    <div className="run-limits-control">
      <div className="row">
        <span className="lbl">Spend cap</span>
        <span className="val">
          {currentBudgetUsd != null ? fmtUsd(currentBudgetUsd) : "Uncapped"}
          {usedUsd != null ? (
            <span className="run-limits-control-used"> · {fmtUsd(usedUsd)} used</span>
          ) : null}
        </span>
      </div>
      <div className="run-limits-control-edit">
        <span className="run-limits-control-prefix">$</span>
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
          <span className="run-limits-control-used"> · {fmtTokens(usedTokens)} used</span>
        </span>
      </div>
      <div className="run-limits-control-edit">
        <span className="run-limits-control-prefix">#</span>
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
      </div>
      <div className="row">
        <span className="lbl">Round cap</span>
        <span className="val">{currentMaxRounds != null ? currentMaxRounds : "Uncapped"}</span>
      </div>
      <div className="run-limits-control-edit">
        <span className="run-limits-control-prefix">R</span>
        <input
          type="number"
          min={0}
          step={1}
          inputMode="numeric"
          value={roundsDraft}
          disabled={disabled || pending}
          placeholder={currentMaxRounds != null ? undefined : "no cap"}
          onChange={(e) => {
            setRoundsDraft(e.target.value);
            setNote(null);
            cmd.clear();
          }}
          aria-label="New round cap"
        />
        {currentMaxRounds != null ? (
          <Button
            className="run-limits-control-lift"
            disabled={disabled || pending}
            onClick={() => void liftRounds()}
            aria-label="Lift the round cap"
          >
            Uncap
          </Button>
        ) : null}
        <button
          type="button"
          className="run-limits-control-set"
          disabled={disabled || pending || !hasChange}
          onClick={onSet}
        >
          {pending ? "Setting…" : "Set caps"}
        </button>
      </div>
      {/* Name the other ceiling rather than infer which one bound — the browser has no authority
          to make that derivation. */}
      <small className="run-limits-control-hint">
        {isHalt
          ? "Halts the run after the current round."
          : "Re-read each round — raise to release, set a cap to 0 to halt. Whichever trips first wins. A spend raise is also clamped by your account allowance (Account → Security)."}
      </small>
      {note ? <small className="run-limits-control-note">{note}</small> : null}
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
