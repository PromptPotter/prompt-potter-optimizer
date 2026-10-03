"use client";
// The affordance for verifying a searchpoint: a candidate's rate is a claim about the panel its
// round bought, not about the dataset.

import { useState } from "react";
import type { SelectedCandidate } from "@/lib/types";
import type { CyclePath } from "@/lib/ids";
import { postVerifyCandidate } from "@/lib/api/commands";
import { useCommand } from "@/lib/hooks/useCommand";

export function VerifyAction({
  candidate,
  path,
}: {
  candidate: SelectedCandidate;
  path: CyclePath | null;
}) {
  const cmd = useCommand<"verify-candidate">("verify-candidate");
  // "Sent" is success WORDING, not a pending state: the verdict lands out of band, so nothing
  // this surface polls ever retires it.
  const [sent, setSent] = useState(false);

  const hop = path?.[0] ?? null;
  // Top level only: `VerifyCandidatePayload` carries no `descend`, so an L4 inner label would
  // match a coincidental id in the OUTER cycle.
  if (!hop || !candidate.label) return null;
  if (path && path.length > 1) return null;

  function run() {
    if (!hop) return;
    void cmd.run(
      "verify-candidate",
      () => postVerifyCandidate(hop.campaignId, hop.cycleId, candidate.label),
      () => setSent(true),
    );
  }

  if (sent) {
    return (
      <p className="verify-action-note">
        Verifying {candidate.label} on cells it has never seen. The verdict lands in{" "}
        <code>diagnostics/runs/</code> — this mints no round, so the cycle&rsquo;s own series
        does not move.
      </p>
    );
  }

  return (
    <>
      <button
        type="button"
        className="btn"
        onClick={run}
        disabled={cmd.pending !== null}
        title={
          "Re-score this candidate on cells it has never been measured on. The count is derived " +
          "from the round budget and how long this cycle has gone unverified, so it stays on the " +
          "scale the campaign already set."
        }
      >
        {cmd.pending !== null ? "Verifying…" : `Verify ${candidate.label}`}
      </button>
      {cmd.failure ? (
        <p className="verify-action-note error">{cmd.failure.message}</p>
      ) : null}
    </>
  );
}
