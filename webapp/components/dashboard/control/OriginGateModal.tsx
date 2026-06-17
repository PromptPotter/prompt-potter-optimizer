"use client";
import { useState } from "react";
import { Button, Dialog } from "@/components/ui";
import {
  postOriginGateDecision,
  IngestApiError,
  type OriginGateDecision,
} from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { fmtPct0 } from "@/lib/format";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";

// The round-0 origin gate. When the freshly-scored origin verdict isn't
// `healthy`, the runner holds before L1 (`run_phase: gate`) instead of
// optimizing against a broken floor — the common case while a developer brings
// up a new connector. This modal is the operator's decision surface; CLI +
// notebook drive the SAME `origin-gate-decision` command, so the ledger stays
// the one source of truth.
//
//   rescore — re-measure the origin force-fresh (reflecting a backend-code fix)
//             and re-evaluate the gate in place. The iterate loop: fix the
//             connector, rescore, watch the verdict — no re-mint.
//   proceed — override the gate and enter L1 knowingly.
//   abort   — end the cycle (StopReason.ORIGIN_GATE).
//
// The decision drives a runner side-effect, not a UI close: the modal stays open
// until the poll observes `run_phase` leave `gate` (a rescore re-enters it with a
// fresh verdict; proceed/abort end it).
export function OriginGateModal() {
  const { dash } = useDashboard();
  const { campaignId, cycleId } = useWorkspace();
  const [pending, setPending] = useState<OriginGateDecision | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const isGate = dash?.run_phase === "gate";
  const origin = dash?.rounds?.find((r) => r.round === 0) ?? null;
  const health = origin?.health ?? null;
  const grade = health?.grade ?? "unknown";

  if (!isGate || !campaignId || !cycleId) return null;

  const decide = (decision: OriginGateDecision) => {
    setPending(decision);
    setErr(null);
    postOriginGateDecision(campaignId, cycleId, decision)
      .then(() => bumpRevalidation())
      .catch((e) => setErr(IngestApiError.toOperatorMessage(e)))
      .finally(() => setPending(null));
  };

  const busy = pending !== null;

  return (
    <Dialog
      open
      title={`Origin gate — verdict: ${grade}`}
      onClose={() => {}}
      footer={
        <>
          <Button variant="danger" disabled={busy} onClick={() => decide("abort")}>
            {pending === "abort" ? "Aborting…" : "Abort"}
          </Button>
          <Button variant="ghost" disabled={busy} onClick={() => decide("proceed")}>
            {pending === "proceed" ? "Proceeding…" : "Proceed anyway"}
          </Button>
          <Button variant="primary" disabled={busy} onClick={() => decide("rescore")}>
            {pending === "rescore" ? "Re-scoring…" : "Re-score origin"}
          </Button>
        </>
      }
    >
      <p className="origin-gate-lead">
        The origin (round 0) scored <strong>{grade}</strong> — not healthy enough to
        optimize against. The run is holding before L1. Fix the connector and{" "}
        <strong>re-score</strong> to re-check, <strong>proceed</strong> to optimize
        anyway, or <strong>abort</strong>.
      </p>
      {health ? (
        <dl className="origin-gate-verdict">
          <div>
            <dt>Degraded rate</dt>
            <dd>{fmtPct0(health.degraded_rate)}</dd>
          </div>
          <div>
            <dt>Structural / transient</dt>
            <dd>
              {health.structural_count} / {health.transient_count}
            </dd>
          </div>
          {health.dominant_node ? (
            <div>
              <dt>Worst node</dt>
              <dd>{health.dominant_node}</dd>
            </div>
          ) : null}
          {health.suggested_action ? (
            <div>
              <dt>Suggested fix</dt>
              <dd>{health.suggested_action}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}
      {health?.reasons?.length ? (
        <ul className="origin-gate-reasons">
          {health.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      ) : null}
      {err ? (
        <p className="origin-gate-err" role="alert">
          {err}
        </p>
      ) : null}
    </Dialog>
  );
}
