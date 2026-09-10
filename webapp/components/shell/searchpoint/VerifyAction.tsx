"use client";
// The affordance for verifying a searchpoint. A candidate's rate is read on the cells that round's
// acquisition bought, so a headline — 100% most of all — is a claim about a panel, not about the
// dataset; settling it used to take a terminal, which our deployment tier does not have.

import { useState } from "react";
import type { SelectedCandidate } from "@/lib/types";
import type { CyclePath } from "@/lib/ids";
import { postVerifyCandidate } from "@/lib/api/commands";

export function VerifyAction({
  candidate,
  path,
}: {
  candidate: SelectedCandidate;
  // The searchpoint's own address, `null` where the host has no address to name.
  path: CyclePath | null;
}) {
  const [state, setState] = useState<"idle" | "sending" | "sent" | "failed">("idle");
  const [detail, setDetail] = useState("");

  const hop = path?.[0] ?? null;
  // Top level only, exactly like the fork beside it: `VerifyCandidatePayload` extends
  // `CyclePayload`, so it carries no `descend` and an L4 inner label would match a coincidental id
  // in the OUTER cycle and score the wrong config. Widening it means widening the command first.
  if (!hop || !candidate.label) return null;
  if (path && path.length > 1) return null;

  async function run() {
    if (!hop) return;
    setState("sending");
    try {
      await postVerifyCandidate(hop.campaignId, hop.cycleId, candidate.label);
      setState("sent");
    } catch (e) {
      setState("failed");
      setDetail(e instanceof Error ? e.message : String(e));
    }
  }

  if (state === "sent") {
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
        disabled={state === "sending"}
        title={
          "Re-score this candidate on cells it has never been measured on. The count is derived " +
          "from the round budget and how long this cycle has gone unverified, so it stays on the " +
          "scale the campaign already set."
        }
      >
        {state === "sending" ? "Verifying…" : `Verify ${candidate.label}`}
      </button>
      {state === "failed" ? (
        <p className="verify-action-note error">{detail}</p>
      ) : null}
    </>
  );
}
