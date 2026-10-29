"use client";

import { useState } from "react";
import type { SelectedCandidate } from "@/lib/types";
import type { CyclePath } from "@/lib/ids";
import { cycleAddress, postCommand, type VerifyStrategy } from "@/lib/api/commands";
import { VERIFY_STRATEGY_LABELS } from "@/lib/api/types.generated";
import { VERIFY_PASS_POLL_MS } from "@/lib/cadence";
import { useCommand } from "@/lib/hooks/useCommand";
import { useVerify } from "@/lib/hooks/useVerify";
import { unitCount } from "@/lib/format";
import {
  Chip,
  CommitInput,
  FailureLine,
  IconMore,
  Menu,
  MenuRadioGroup,
  MenuSep,
} from "@/components/ui";
import { VerifyReading } from "./VerifyReading";

const STRATEGIES = (Object.keys(VERIFY_STRATEGY_LABELS) as VerifyStrategy[]).map((value) => ({
  value,
  label: VERIFY_STRATEGY_LABELS[value],
}));

export function VerifyAction({
  candidate,
  path,
}: {
  candidate: SelectedCandidate;
  path: CyclePath | null;
}) {
  const cmd = useCommand<"verify-candidate">("verify-candidate");
  const [strategy, setStrategy] = useState<VerifyStrategy>("random");
  const [samples, setSamples] = useState("");
  const sending = cmd.pending !== null;
  const { readings, passes, unit } = useVerify(path, sending ? VERIFY_PASS_POLL_MS : undefined);

  if (!path || !candidate.label) return null;

  const reading = readings.get(candidate.arm) ?? null;
  const last = passes.get(candidate.arm) ?? null;
  const flying = last?.state === "running" ? last : null;
  const busy = sending || flying !== null;

  function run() {
    if (!path) return;
    void cmd.run("verify-candidate", () =>
      // `samples` omitted is the server's count (`verify.py::derive_verify_samples`).
      postCommand("verify-candidate", {
        ...cycleAddress(path),
        candidate_id: candidate.arm,
        strategy,
        ...(samples === "" ? {} : { samples: Number(samples) }),
      }),
    );
  }

  return (
    <div className="verify-action">
      <div className="verify-action-row">
        <button
          type="button"
          className="btn"
          onClick={run}
          disabled={busy}
          title="Re-score this candidate on search cells it has never been measured on."
        >
          {busy ? "Verifying…" : `Verify ${candidate.label}`}
        </button>
        <Menu
          align="left"
          renderTrigger={({ open, toggle }) => (
            <Chip
              icon
              on={open || strategy !== "random" || samples !== ""}
              ariaLabel="Verify options"
              title="Which cells a verify picks, and how many"
              onClick={toggle}
            >
              <IconMore />
            </Chip>
          )}
        >
          {() => (
            <>
              <MenuRadioGroup
                label="Fresh cells picked"
                value={strategy}
                options={STRATEGIES}
                onChange={setStrategy}
              />
              <MenuSep />
              <label className="verify-samples">
                <span>cells</span>
                <CommitInput
                  type="text"
                  inputMode="numeric"
                  value={samples}
                  placeholder="derived"
                  aria-label="Fresh cells to measure; blank lets the round budget decide"
                  validate={(d) => d === "" || /^[1-9]\d*$/.test(d)}
                  onCommit={setSamples}
                />
              </label>
              <p className="verify-samples-note">
                Blank takes the count from the round budget and how long this cycle has gone
                unverified. A larger one is refused.
              </p>
            </>
          )}
        </Menu>
      </div>
      {busy && (
        <p className="verify-action-note">
          {flying && unit
            ? `Measuring ${flying.arm.label} on ${unitCount(flying.rows, unit)} it has never seen, ${flying.scored} scored.`
            : `Measuring ${candidate.label} on cells it has never seen.`}{" "}
          This mints no round, so the cycle&rsquo;s own series does not move.
        </p>
      )}
      <FailureLine failure={cmd.failure} />
      {!busy && last?.failure != null && (
        <p className="verify-action-note error">The last verify failed: {last.failure}</p>
      )}
      {reading && unit && <VerifyReading reading={reading} unit={unit} />}
    </div>
  );
}
