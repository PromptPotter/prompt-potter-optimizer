"use client";
import { useState } from "react";
import { cycleAddress, postCommand, type Budget, type MeteredSpend } from "@/lib/api";
import { CEILING_METER_LABELS } from "@/lib/api/types.generated";
import { budgetAxis } from "@/lib/derivations";
import { useCommand } from "@/lib/hooks/useCommand";
import { fmtDuration, fmtUsd, fmtTokens } from "@/lib/format";
import { parseCap } from "@/lib/run-limits";
import { useWorkspace } from "@/lib/workspace";
import { Modal } from "@/components/shell/Modal";
import { Button, FailureLine } from "@/components/ui";

type Axis = keyof Budget;

interface AxisSpec {
  axis: Axis;
  label: string;
  prefix: string;
  step: number;
  int: boolean;
  // The input's unit over the wire's: the wall clock is typed in minutes and sent in seconds.
  scale: number;
  min: number;
  show: (v: number) => string;
  ariaLabel: string;
}

const AXES: AxisSpec[] = [
  { axis: "usd", label: "Spend cap", prefix: "$", step: 0.01, int: false, scale: 1, min: 0, show: fmtUsd, ariaLabel: "New campaign spend cap in USD" },
  { axis: "tokens", label: "Token cap", prefix: "#", step: 1000, int: true, scale: 1, min: 0, show: fmtTokens, ariaLabel: "New campaign token cap" },
  { axis: "rounds", label: "Round cap", prefix: "R", step: 1, int: true, scale: 1, min: 0, show: String, ariaLabel: "New round cap" },
  { axis: "wall_clock_s", label: "Wall-clock cap", prefix: "min", step: 5, int: false, scale: 60, min: Number.MIN_VALUE, show: fmtDuration, ariaLabel: "New wall-clock cap in minutes" },
];

type Drafts = Record<Axis, string>;

function draftsOf(budget: Budget): Drafts {
  const out = {} as Drafts;
  for (const { axis, scale } of AXES) {
    const v = budgetAxis(budget, axis);
    out[axis] = v != null ? String(v / scale) : "";
  }
  return out;
}

function budgetKey(budget: Budget): string {
  return AXES.map(({ axis }) => budgetAxis(budget, axis)).join("|");
}

// The cap is the campaign's; `share` is this cycle's part of it, what its run's book binds.
function usedText(axis: Axis, metered: MeteredSpend | null, share: Budget | null): string | null {
  if (!metered) return null;
  const of = share ? budgetAxis(share, axis) : null;
  if (axis === "usd") {
    const part = of != null ? ` of its ${fmtUsd(of)} share` : "";
    return `${fmtUsd(metered.metered_usd)} this cycle${part}, ${CEILING_METER_LABELS[metered.meter]}`;
  }
  if (axis === "tokens") {
    return `${fmtTokens(metered.metered_tokens)} this cycle${of != null ? ` of its ${fmtTokens(of)} share` : ""}`;
  }
  return null;
}

interface Props {
  budget: Budget;
  share: Budget | null;
  metered: MeteredSpend | null;
}

export function RunLimitsControl({ budget, share, metered }: Props) {
  const { viewedPath, leafCycleId } = useWorkspace();
  const [drafts, setDrafts] = useState<Drafts>(() => draftsOf(budget));
  // Never remounted on a unit switch: unseeded, `apply()` writes the prior cycle's caps on the new.
  const key = budgetKey(budget);
  const [prevKey, setPrevKey] = useState(key);
  if (key !== prevKey) {
    setPrevKey(key);
    setDrafts(draftsOf(budget));
  }
  const cmd = useCommand<"change-run-limits">("run-limits", { scope: leafCycleId });
  const [confirmingHalt, setConfirmingHalt] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const pending = cmd.pending !== null;
  const disabled = !viewedPath;

  // Only a CHANGED axis is named: an unnamed one stays as it stands, and a named null would lift it.
  const next: Budget = {};
  for (const { axis, int, scale, min } of AXES) {
    const typed = parseCap(drafts[axis], { int, min });
    const value = typed == null ? null : typed * scale;
    if (value != null && value !== budgetAxis(budget, axis)) next[axis] = value;
  }
  const hasChange = Object.keys(next).length > 0;
  const isHalt = next.usd === 0 || next.tokens === 0 || next.rounds === 0;

  const send = async (change: Budget, applied: string) => {
    if (!viewedPath) return;
    setConfirmingHalt(false);
    setNote(null);
    const r = await cmd.run("change-run-limits", () =>
      postCommand("change-run-limits", { ...cycleAddress(viewedPath), budget: change }),
    );
    if (r.ok) setNote(applied);
  };

  // Quotes NO number: `quota.py::clamp_budget_change` mins the request against the allowance.
  const apply = () =>
    void send(
      next,
      isHalt
        ? "Applied — halting after this round."
        : "Applied — the armed cap is shown above; it takes at the next round.",
    );

  const onSet = () => {
    if (!hasChange) return;
    if (isHalt) {
      setConfirmingHalt(true);
      return;
    }
    apply();
  };

  return (
    <div className="run-limits-control">
      {AXES.map((spec) => {
        const current = budgetAxis(budget, spec.axis);
        const used = usedText(spec.axis, metered, share);
        return (
          <div key={spec.axis}>
            <div className="row">
              <span className="lbl">{spec.label}</span>
              <span className="val">
                {current != null ? spec.show(current) : "Uncapped"}
                {used ? <span className="run-limits-control-used"> · {used}</span> : null}
              </span>
            </div>
            <div className="run-limits-control-edit">
              <span className="run-limits-control-prefix">{spec.prefix}</span>
              <input
                type="number"
                min={0}
                step={spec.step}
                inputMode={spec.int ? "numeric" : "decimal"}
                value={drafts[spec.axis]}
                disabled={disabled || pending}
                placeholder={current != null ? undefined : "no cap"}
                onChange={(e) => {
                  const value = e.target.value;
                  setDrafts((d) => ({ ...d, [spec.axis]: value }));
                  setNote(null);
                  cmd.clear();
                }}
                aria-label={spec.ariaLabel}
              />
              {current != null ? (
                <Button
                  className="run-limits-control-lift"
                  disabled={disabled || pending}
                  onClick={() =>
                    void send(
                      { [spec.axis]: null },
                      `Applied — no ${spec.label.toLowerCase()} from the next round; the other caps govern.`,
                    )
                  }
                  aria-label={`Lift the ${spec.label.toLowerCase()}`}
                >
                  Uncap
                </Button>
              ) : null}
            </div>
          </div>
        );
      })}
      <div className="run-limits-control-edit">
        <button
          type="button"
          className="run-limits-control-set"
          disabled={disabled || pending || !hasChange}
          onClick={onSet}
        >
          {pending ? "Setting…" : "Set caps"}
        </button>
      </div>
      <small className="run-limits-control-hint">
        {isHalt
          ? "Halts the run after the current round."
          : "Re-read each round — raise to release, set a cap to 0 to halt. Whichever trips first wins. Spend and token caps are the campaign's, one book its live cycles share; the round cap is this cycle's; the wall clock bounds one launch and restarts at the next, cutting a remote cell still open at its edge. A spend raise is also clamped by your account allowance (Account → Security)."}
      </small>
      {note ? <small className="run-limits-control-note">{note}</small> : null}
      <FailureLine failure={cmd.failure} />
      <Modal
        open={confirmingHalt}
        title="Halt this run?"
        message={`Setting a cap to 0 stops ${leafCycleId ?? "this unit"} after the current round completes. Measurements so far are preserved; you can resume later by raising the cap and re-running.`}
        actions={[
          { label: "Cancel", onClick: () => setConfirmingHalt(false) },
          { label: "Set 0 & halt", variant: "danger", onClick: apply },
        ]}
        onClose={() => setConfirmingHalt(false)}
      />
    </div>
  );
}
