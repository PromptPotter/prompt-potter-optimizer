"use client";
// The affordance for verifying a searchpoint. A candidate's rate is read on the cells that round's
// acquisition bought, so a headline — 100% most of all — is a claim about a panel, not about the
// dataset; settling it used to take a terminal, which our deployment tier does not have.

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
  // The searchpoint's own address, `null` where the host has no address to name.
  path: CyclePath | null;
}) {
  const cmd = useCommand<"verify-candidate">("verify-candidate");
  // "Sent" is success WORDING, not a pending state: the verdict lands out of band, so nothing
  // this surface polls ever retires it.
  const [sent, setSent] = useState(false);

  const hop = path?.[0] ?? null;
  // Top level only, exactly like the fork beside it: `VerifyCandidatePayload` extends
  // `CyclePayload`, so it carries no `descend` and an L4 inner label would match a coincidental id
  // in the OUTER cycle and score the wrong config. Widening it means widening the command first.
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
