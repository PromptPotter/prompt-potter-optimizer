"use client";
import { useState } from "react";
import { postOriginGateDecision, IngestApiError, type OriginGateDecision } from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { fmtPct0 } from "@/lib/format";
import { cx } from "@/lib/cx";
import type { ActivityItem } from "@/lib/chat/activity";
import type { DecisionItem, GateVerdict } from "@/lib/chat/decision";

// The live tail of the one chat thread: the curated activity feed + the
// transient progress chip + the inline decision card. This is the surface where
// the parallel chat ↔ cycle-trace streams MERGE — a decision button fires an
// existing control command that lands on the cycle ledger and re-appears in the
// feed as a "control applied" item. Rendered inside `IngestConversation`'s
// thread, after the ingest messages — one ordered conversation.
export function LiveSegment({
  campaignId,
  cycleId,
  activity,
  progress,
  connected,
  decision,
}: {
  campaignId: string;
  cycleId: string;
  activity: ActivityItem[];
  progress: ActivityItem | null;
  connected: boolean;
  decision: DecisionItem | null;
}) {
  const [pending, setPending] = useState<OriginGateDecision | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const empty = activity.length === 0 && !progress && !decision;
  // Nothing to show and not even connected — leave the thread to the ingest
  // segment / welcome.
  if (empty && !connected) return null;

  // Fire an existing control command. The decision item clears on its own when
  // the poll observes `run_phase` leave `gate` (a rescore re-enters with a fresh
  // verdict; proceed/abort end it) — same lifecycle the old modal had.
  const decide = (d: OriginGateDecision) => {
    setPending(d);
    setErr(null);
    postOriginGateDecision(campaignId, cycleId, d)
      .then(() => bumpRevalidation())
      .catch((e) => setErr(IngestApiError.toOperatorMessage(e)))
      .finally(() => setPending(null));
  };
  const busy = pending !== null;

  return (
    <div className="chat-live">
      {empty && connected ? (
        <div className="chat-activity tone-muted kind-progress" role="status" aria-live="polite">
          <span className="chat-activity-icon" aria-hidden="true">
            ·
          </span>
          <span className="chat-activity-label">Listening for activity…</span>
        </div>
      ) : null}
      {activity.map((a) => (
        <div
          key={a.id}
          className={cx("chat-activity", `tone-${a.tone ?? "muted"}`, `kind-${a.kind}`)}
        >
          <span className="chat-activity-icon" aria-hidden="true">
            {a.icon}
          </span>
          <span className="chat-activity-label">{a.label}</span>
          {a.detail ? <span className="chat-activity-detail">{a.detail}</span> : null}
        </div>
      ))}

      {progress ? (
        <div
          className="chat-activity tone-muted kind-progress"
          role="status"
          aria-live="polite"
        >
          <span className="chat-activity-icon" aria-hidden="true">
            {progress.icon}
          </span>
          <span className="chat-activity-label">{progress.label}</span>
        </div>
      ) : null}

      {decision ? (
        <div className="chat-msg ai chat-decision" role="group" aria-label={decision.title}>
          <div className="chat-decision-title">{decision.title}</div>
          <p className="chat-decision-lead">{decision.lead}</p>
          {decision.verdict ? <GateVerdictView verdict={decision.verdict} /> : null}
          <div className="chat-decision-actions">
            {decision.buttons.map((b) => (
              <button
                key={b.decision}
                type="button"
                className={cx("chat-decision-btn", `btn-${b.variant}`)}
                disabled={busy}
                onClick={() => decide(b.decision)}
              >
                {pending === b.decision ? "…" : b.label}
              </button>
            ))}
          </div>
          {err ? (
            <p className="chat-decision-err" role="alert">
              {err}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// The origin verdict, folded in from the deleted `OriginGateModal` so no
// diagnostic is lost when the gate decision moved into the chat.
function GateVerdictView({ verdict }: { verdict: GateVerdict }) {
  return (
    <>
      <dl className="chat-decision-verdict">
        {verdict.degradedRate != null ? (
          <div>
            <dt>Degraded rate</dt>
            <dd>{fmtPct0(verdict.degradedRate)}</dd>
          </div>
        ) : null}
        {verdict.structuralCount != null || verdict.transientCount != null ? (
          <div>
            <dt>Structural / transient</dt>
            <dd>
              {verdict.structuralCount ?? 0} / {verdict.transientCount ?? 0}
            </dd>
          </div>
        ) : null}
        {verdict.dominantNode ? (
          <div>
            <dt>Worst node</dt>
            <dd>{verdict.dominantNode}</dd>
          </div>
        ) : null}
        {verdict.suggestedAction ? (
          <div>
            <dt>Suggested fix</dt>
            <dd>{verdict.suggestedAction}</dd>
          </div>
        ) : null}
      </dl>
      {verdict.reasons.length ? (
        <ul className="chat-decision-reasons">
          {verdict.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      ) : null}
    </>
  );
}
