import { useState } from "react";
import { commands, cycleAddress } from "@/lib/api";
import {
  type Budget,
  type CapReading,
  type Overshoot,
  CAP_AXIS_INFO,
  CAP_BOOK_WORDS,
  CAP_CLAMP_WORDS,
  CEILING_METER_LABELS,
  type CapAxis,
} from "@/lib/api/types";
import { useCommand } from "@/lib/hooks/useCommand";
import { fmtUsd } from "@/lib/format";
import { capInput, capWords, parseCapIn, type Cap } from "@/lib/run-limits";
import { useWorkspace } from "@/lib/state/workspace";
import { Button, Dialog, FailureLine, Switch } from "@/components/ui";

type Drafts = Record<CapAxis, string>;

function draftsOf(caps: CapReading[]): Drafts {
  const out = {} as Drafts;
  for (const cap of caps) {
    out[cap.axis] = cap.armed != null ? String(cap.armed / CAP_AXIS_INFO[cap.axis].scale) : "";
  }
  return out;
}

function capsKey(caps: CapReading[]): string {
  return caps.map((cap) => cap.armed).join("|");
}

// What the cap's own book has used, against the cap where one stands.
function usedText({ axis, armed, used }: CapReading): string | null {
  if (used == null) return null;
  const word = CAP_AXIS_INFO[axis].used;
  return armed == null
    ? `${capWords(axis, used)} ${word}`
    : `${capWords(axis, used)} of ${capWords(axis, armed)} ${word}`;
}

function bookText({ axis, meter }: CapReading): string {
  const book = CAP_BOOK_WORDS[CAP_AXIS_INFO[axis].book];
  return meter ? `${book} · ${CEILING_METER_LABELS[meter]}` : book;
}

interface Props {
  caps: CapReading[];
  overshoot: boolean;
  overshot: Overshoot | null;
}

export function RunLimitsControl({ caps, overshoot, overshot }: Props) {
  const { viewed } = useWorkspace();
  const viewedPath = viewed?.path ?? null;
  const [drafts, setDrafts] = useState<Drafts>(() => draftsOf(caps));
  // Never remounted on a unit switch: unseeded, `apply()` writes the prior cycle's caps on the new.
  const key = capsKey(caps);
  const [prevKey, setPrevKey] = useState(key);
  if (key !== prevKey) {
    setPrevKey(key);
    setDrafts(draftsOf(caps));
  }
  const cmd = useCommand<"set-limits">("run-limits", viewed?.address ?? null);
  const [confirmingHalt, setConfirmingHalt] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const pending = cmd.pending !== null;
  const disabled = !viewedPath;

  // Only a CHANGED axis is named: an unnamed one stays as it stands, and a named null would lift it.
  const typed = {} as Record<CapAxis, Cap>;
  const next: Budget = {};
  for (const cap of caps) {
    const parsed = (typed[cap.axis] = parseCapIn(drafts[cap.axis], CAP_AXIS_INFO[cap.axis]));
    const value = "value" in parsed ? parsed.value : null;
    if (value != null && value !== cap.armed) next[cap.axis] = value;
  }
  // A refused axis holds Set: sending the rest would read as the whole edit taking.
  const refused = caps.some((cap) => "refused" in typed[cap.axis]);
  const hasChange = !refused && Object.keys(next).length > 0;
  // A refused 0 never reaches `next`, so a 0 there is a legal cap admitting nothing more.
  const isHalt = caps.some((cap) => next[cap.axis] === 0);

  const send = async (change: { budget?: Budget; overshoot?: boolean }, applied: string) => {
    if (!viewedPath) return;
    setConfirmingHalt(false);
    setNote(null);
    const r = await cmd.run("set-limits", () =>
      commands["set-limits"]({ ...cycleAddress(viewedPath), ...change }),
    );
    if (r.ok) setNote(applied);
  };

  // Quotes NO number: the rows re-read what armed, a clamp by the allowance on its own row.
  const apply = () =>
    void send(
      { budget: next },
      isHalt ? "Applied — halting after this round." : "Applied — it takes at the next round.",
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
      {caps.map((cap) => {
        const info = CAP_AXIS_INFO[cap.axis];
        const used = usedText(cap);
        const label = info.short;
        return (
          <div key={cap.axis} className="run-limits-row">
            <span className="run-limits-axis">{label}</span>
            <span className="run-limits-input">
              <input
                {...capInput(info)}
                value={drafts[cap.axis]}
                aria-invalid={"refused" in typed[cap.axis] || undefined}
                disabled={disabled || pending}
                placeholder="no cap"
                onChange={(e) => {
                  const value = e.target.value;
                  setDrafts((d) => ({ ...d, [cap.axis]: value }));
                  setNote(null);
                  cmd.clear();
                }}
                aria-label={`${info.title} in ${info.unit}`}
              />
              <span className="run-limits-unit">{info.unit}</span>
            </span>
            <span className="run-limits-used">
              {used}
              {cap.fill != null ? (
                <>
                  <meter min={0} max={1} value={Math.min(cap.fill, 1)} aria-label={`${label} used`} />
                  {`${Math.round(cap.fill * 100)}%`}
                </>
              ) : null}
            </span>
            <small className="run-limits-book">{bookText(cap)}</small>
            {cap.armed != null ? (
              <Button
                className="run-limits-lift"
                disabled={disabled || pending}
                onClick={() =>
                  void send(
                    { budget: { [cap.axis]: null } },
                    `Applied — no ${info.title.toLowerCase()} from the next round; the other caps govern.`,
                  )
                }
                aria-label={`Lift the ${info.title.toLowerCase()}`}
              >
                Uncap
              </Button>
            ) : null}
            {cap.clamped_by != null && cap.asked != null && cap.armed != null ? (
              <small className="run-limits-clamp">
                {`asked ${capWords(cap.axis, cap.asked)} → armed ${capWords(cap.axis, cap.armed)}: ${CAP_CLAMP_WORDS[cap.clamped_by]}`}
              </small>
            ) : null}
          </div>
        );
      })}
      <div className="row">
        <span className="lbl">Hold calls at mean bill</span>
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
          {overshot && overshot.calls > 0 ? (
            <span className="run-limits-used">
              {` · ${overshot.calls} ${overshot.calls === 1 ? "call" : "calls"} past their hold by ${fmtUsd(overshot.usd)}`}
            </span>
          ) : null}
        </span>
      </div>
      <div className="run-limits-actions">
        {isHalt ? <small className="run-limits-book">Halts the run after the current round.</small> : null}
        <button
          type="button"
          className="run-limits-set"
          disabled={disabled || pending || !hasChange}
          onClick={onSet}
        >
          {pending ? "Setting…" : "Set caps"}
        </button>
      </div>
      {note ? <small className="run-limits-note">{note}</small> : null}
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
