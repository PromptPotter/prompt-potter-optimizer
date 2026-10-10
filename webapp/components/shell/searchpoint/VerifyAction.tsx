"use client";

import { useState } from "react";
import type { SelectedCandidate } from "@/lib/types";
import type { CyclePath } from "@/lib/ids";
import { postVerifyCandidate, type VerifyStrategy } from "@/lib/api/commands";
import { VERIFY_STRATEGY_LABELS } from "@/lib/api/types.generated";
import { useCommand } from "@/lib/hooks/useCommand";
import { useVerify } from "@/lib/hooks/useVerify";
import { unitCount } from "@/lib/format";
import { Chip, CommitInput, IconMore, Menu, MenuRadioGroup, MenuSep } from "@/components/ui";
import { VerifyReading } from "./VerifyReading";

const PASS_POLL_MS = 5000;

const STRATEGIES: readonly { value: VerifyStrategy; label: string }[] = [
  { value: "random", label: "at random — reads the level" },
  { value: "hard", label: "hardest first — a stress read" },
];

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
  const { readings, pass, unit } = useVerify(path, sending ? PASS_POLL_MS : undefined);

  if (!path || !candidate.label) return null;

  const reading = readings.get(candidate.label) ?? null;
  const flying = pass !== null && pass.label === candidate.label ? pass : null;
  const busy = sending || flying !== null;

  function run() {
    if (!path) return;
    void cmd.run("verify-candidate", () =>
      postVerifyCandidate(path, candidate.candidate_id, {
        strategy,
        samples: samples === "" ? null : Number(samples),
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
                  validate={(d) => d === "" || /^[1-9]\d{0,3}$/.test(d)}
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
            ? `Measuring ${flying.label} on ${unitCount(flying.rows, unit)} it has never seen, ${VERIFY_STRATEGY_LABELS[flying.strategy]}.`
            : `Measuring ${candidate.label} on cells it has never seen.`}{" "}
          This mints no round, so the cycle&rsquo;s own series does not move.
        </p>
      )}
      {cmd.failure ? <p className="verify-action-note error">{cmd.failure.message}</p> : null}
      {reading && unit && <VerifyReading reading={reading} unit={unit} />}
    </div>
  );
}
