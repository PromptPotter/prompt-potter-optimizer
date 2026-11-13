import { useState } from "react";
import { commands, cycleAddress } from "@/lib/api";
import {
  type Budget,
  type MeteredSpend,
  ACCOUNT_TAB_INFO,
  CAP_AXIS_INFO,
  CEILING_METER_LABELS,
  type CapAxis,
} from "@/lib/api/types";
import { wordKeys } from "@/lib/api/words";
import { budgetAxis } from "@/lib/derivations";
import { useCommand } from "@/lib/hooks/useCommand";
import { fmtUnit, fmtUsd } from "@/lib/format";
import { capInput, capText, parseCapIn, type Cap } from "@/lib/run-limits";
import { useWorkspace } from "@/lib/workspace";
import { Button, Dialog, FailureLine, Switch } from "@/components/ui";

const AXES = wordKeys(CAP_AXIS_INFO);

type Drafts = Record<CapAxis, string>;

function draftsOf(budget: Budget): Drafts {
  const out = {} as Drafts;
  for (const axis of AXES) {
    const v = budgetAxis(budget, axis);
    out[axis] = v != null ? String(v / CAP_AXIS_INFO[axis].scale) : "";
  }
  return out;
}

function budgetKey(budget: Budget): string {
  return AXES.map((axis) => budgetAxis(budget, axis)).join("|");
}

// The cap is the campaign's; `share` is this cycle's part of it, what its run's book binds.
function usedText(axis: CapAxis, metered: MeteredSpend | null, share: Budget | null): string | null {
  if (!metered) return null;
  const of = share ? budgetAxis(share, axis) : null;
  if (axis === "usd") {
    const part = of != null ? ` of its ${fmtUsd(of)} share` : "";
    return `${fmtUsd(metered.metered_usd)} this cycle${part}, ${CEILING_METER_LABELS[metered.meter]}`;
  }
  if (axis === "tokens") {
    return `${fmtUnit("tokens", metered.metered_tokens)} this cycle${of != null ? ` of its ${fmtUnit("tokens", of)} share` : ""}`;
  }
  return null;
}

interface Props {
  budget: Budget;
  overshoot: boolean;
  share: Budget | null;
  metered: MeteredSpend | null;
}

export function RunLimitsControl({ budget, overshoot, share, metered }: Props) {
  const { viewed } = useWorkspace();
  const viewedPath = viewed?.path ?? null;
  const [drafts, setDrafts] = useState<Drafts>(() => draftsOf(budget));
  // Never remounted on a unit switch: unseeded, `apply()` writes the prior cycle's caps on the new.
  const key = budgetKey(budget);
  const [prevKey, setPrevKey] = useState(key);
  if (key !== prevKey) {
    setPrevKey(key);
    setDrafts(draftsOf(budget));
  }
  const cmd = useCommand<"set-limits">("run-limits", { scope: viewed?.leaf.cycleId ?? null });
  const [confirmingHalt, setConfirmingHalt] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const pending = cmd.pending !== null;
  const disabled = !viewedPath;

  // Only a CHANGED axis is named: an unnamed one stays as it stands, and a named null would lift it.
  const typed = {} as Record<CapAxis, Cap>;
  const next: Budget = {};
  for (const axis of AXES) {
    const cap = (typed[axis] = parseCapIn(drafts[axis], CAP_AXIS_INFO[axis]));
    const value = "value" in cap ? cap.value : null;
    if (value != null && value !== budgetAxis(budget, axis)) next[axis] = value;
  }
  // A refused axis holds Set: sending the rest would read as the whole edit taking.
  const refused = AXES.some((axis) => "refused" in typed[axis]);
  const hasChange = !refused && Object.keys(next).length > 0;
  // A refused 0 never reaches `next`, so a 0 there is a legal cap admitting nothing more.
  const isHalt = AXES.some((axis) => next[axis] === 0);

  const send = async (change: { budget?: Budget; overshoot?: boolean }, applied: string) => {
    if (!viewedPath) return;
    setConfirmingHalt(false);
    setNote(null);
    const r = await cmd.run("set-limits", () =>
      commands["set-limits"]({ ...cycleAddress(viewedPath), ...change }),
    );
    if (r.ok) setNote(applied);
  };

  // Quotes NO number: `quota.py::change_budget` mins the request against the allowance.
  const apply = () =>
    void send(
      { budget: next },
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
      {AXES.map((axis) => {
        const spec = { axis, ...CAP_AXIS_INFO[axis] };
        const current = budgetAxis(budget, axis);
        const used = usedText(axis, metered, share);
        return (
          <div key={axis}>
            <div className="row">
              <span className="lbl">{spec.title}</span>
              <span className="val">
                {current != null ? capText(spec, current) : "Uncapped"}
                {used ? <span className="run-limits-control-used"> · {used}</span> : null}
              </span>
            </div>
            <div className="run-limits-control-edit">
              <span className="run-limits-control-prefix">{spec.unit}</span>
              <input
                {...capInput(spec)}
                value={drafts[spec.axis]}
                aria-invalid={"refused" in typed[axis] || undefined}
                disabled={disabled || pending}
                placeholder={current != null ? undefined : "no cap"}
                onChange={(e) => {
                  const value = e.target.value;
                  setDrafts((d) => ({ ...d, [spec.axis]: value }));
                  setNote(null);
                  cmd.clear();
                }}
                aria-label={`${spec.title} in ${spec.unit}`}
              />
              {current != null ? (
                <Button
                  className="run-limits-control-lift"
                  disabled={disabled || pending}
                  onClick={() =>
                    void send(
                      { budget: { [spec.axis]: null } },
                      `Applied — no ${spec.title.toLowerCase()} from the next round; the other caps govern.`,
                    )
                  }
                  aria-label={`Lift the ${spec.title.toLowerCase()}`}
                >
                  Uncap
                </Button>
              ) : null}
            </div>
          </div>
        );
      })}
      <div className="row">
        <span className="lbl">Overshoot</span>
        <span className="val">
          <Switch
            checked={overshoot}
            label="Hold each call at its node's mean bill"
            locked={disabled || pending}
            lockedNote="saving"
            onChange={() =>
              void send(
                { overshoot: !overshoot },
                overshoot
                  ? "Applied — every call holds its worst case again."
                  : "Applied — a call holds its node's mean bill once one has billed.",
              )
            }
          />
        </span>
      </div>
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
          : `Re-read each round — raise to release, set a cap to 0 to halt. Whichever trips first wins. Spend and token caps are the campaign's, one book its live cycles share; the round cap is this cycle's; the wall clock bounds one launch and restarts at the next, cutting a remote cell still open at its edge. A spend raise is also clamped by your account allowance (${ACCOUNT_TAB_INFO.usage.place}).`}
      </small>
      {note ? <small className="run-limits-control-note">{note}</small> : null}
      <FailureLine failure={cmd.failure} />
      <Dialog
        open={confirmingHalt}
        title="Halt this run?"
        onClose={() => setConfirmingHalt(false)}
        footer={
          <>
            <Button onClick={() => setConfirmingHalt(false)}>Cancel</Button>
            <Button variant="danger" onClick={apply}>
              Set 0 &amp; halt
            </Button>
          </>
        }
      >
        <p>{`Setting a cap to 0 stops ${viewed?.leaf.cycleId ?? "this unit"} after the current round completes. Measurements so far are preserved; you can resume later by raising the cap and re-running.`}</p>
      </Dialog>
    </div>
  );
}
