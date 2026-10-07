"use client";
// Verifying a searchpoint, and the answer beside the button that asked: a candidate's rate is a
// claim about the panel its round bought, not about the dataset.

import { useState } from "react";
import type { SelectedCandidate } from "@/lib/types";
import type { CyclePath } from "@/lib/ids";
import { postVerifyCandidate } from "@/lib/api/commands";
import { useCommand } from "@/lib/hooks/useCommand";
import { useVerifyRuns } from "@/lib/hooks/useVerifyRuns";
import { VerifyReading } from "./VerifyReading";

const AWAIT_POLL_MS = 5000;

export function VerifyAction({
  candidate,
  path,
}: {
  candidate: SelectedCandidate;
  path: CyclePath | null;
}) {
  const cmd = useCommand<"verify-candidate">("verify-candidate");
  // Top level only: `VerifyCandidatePayload` carries no `descend`, so an L4 inner label would
  // match a coincidental id in the OUTER cycle.
  const hop = path && path.length === 1 ? (path[0] ?? null) : null;

  // The record a send was issued OVER, by subject — a newer one for that label retires the wait.
  // Stamps are compared with each other, never with this browser's clock.
  const [sent, setSent] = useState<{ label: string; over: string } | null>(null);
  const awaitingLabel = sent?.label === candidate.label ? sent : null;
  const runs = useVerifyRuns(
    hop?.campaignId,
    hop?.cycleId,
    awaitingLabel ? AWAIT_POLL_MS : undefined,
  );
  const reading = runs.get(candidate.label) ?? null;
  const awaiting = awaitingLabel !== null && (reading?.ts ?? "") === awaitingLabel.over;

  if (!hop || !candidate.label) return null;

  function run() {
    if (!hop) return;
    const over = reading?.ts ?? "";
    void cmd.run(
      "verify-candidate",
      () => postVerifyCandidate(hop.campaignId, hop.cycleId, candidate.label),
      () => setSent({ label: candidate.label, over }),
    );
  }

  return (
    <div className="verify-action">
      <button
        type="button"
        className="btn"
        onClick={run}
        disabled={cmd.pending !== null || awaiting}
        title={
          "Re-score this candidate on cells it has never been measured on. The count is derived " +
          "from the round budget and how long this cycle has gone unverified, so it stays on the " +
          "scale the campaign already set."
        }
      >
        {cmd.pending !== null || awaiting ? "Verifying…" : `Verify ${candidate.label}`}
      </button>
      {awaiting && (
        <p className="verify-action-note">
          Measuring {candidate.label} on cells it has never seen. This mints no round, so the
          cycle&rsquo;s own series does not move.
        </p>
      )}
      {cmd.failure ? <p className="verify-action-note error">{cmd.failure.message}</p> : null}
      {reading && <VerifyReading run={reading} />}
    </div>
  );
}
