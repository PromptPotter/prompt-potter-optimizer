"use client";
import { useState } from "react";
import { postOriginGateDecision, IngestApiError, type OriginGateDecision } from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { fmtPct0 } from "@/lib/format";
import { cx } from "@/lib/cx";
import { Hearts } from "@/components/ui";
import type { ActivityItem } from "@/lib/chat/activity";
import type { DecisionItem } from "@/lib/chat/decision";
import type { DegradationHealth } from "@/lib/api/types";

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
  listening,
  decision,
  hearts,
  livesCap,
}: {
  campaignId: string;
  cycleId: string;
  activity: ActivityItem[];
  progress: ActivityItem | null;
  /**
   * Is there anything still to listen FOR — the SSE socket being open AND the server
   * still calling the run in-flight. The socket alone is not the answer: it stays open
   * against a finished cycle (the endpoint 404s only for an unknown one), so gating on
   * it had the thread announcing it was listening to a run that had ended.
   */
  listening: boolean;
  decision: DecisionItem | null;
  /** Banked lives of the VIEWED cycle; `null` when it isn't in lives mode. */
  hearts?: number | null;
  /** The bank's ceiling — the denominator. Passed down, never re-derived here. */
  livesCap?: number | null;
}) {
  const [pending, setPending] = useState<OriginGateDecision | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const empty = activity.length === 0 && !progress && !decision;
  // Nothing to show and nothing still coming — leave the thread to the ingest
  // segment / welcome.
  if (empty && !listening) return null;

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
      {empty && listening ? (
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
          {progress.detail ? (
            <span className="chat-activity-detail">{progress.detail}</span>
          ) : null}
          {/* The ♥ bank rides the PROGRESS chip, not the round rows above it: the feed is a
              history, and painting the current bank onto a finished round would misdate it.
              The chip is the one row that means "now". */}
          {hearts != null && (
            <Hearts hearts={hearts} cap={livesCap} className="chat-activity-hearts" />
          )}
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
function GateVerdictView({ verdict }: { verdict: DegradationHealth }) {
  return (
    <>
      <dl className="chat-decision-verdict">
        <div>
          <dt>Degraded rate</dt>
          <dd>{fmtPct0(verdict.degraded_rate)}</dd>
        </div>
        <div>
          <dt>Structural / transient</dt>
          <dd>
            {verdict.structural_count} / {verdict.transient_count}
          </dd>
        </div>
        {verdict.dominant_node ? (
          <div>
            <dt>Worst node</dt>
            <dd>{verdict.dominant_node}</dd>
          </div>
        ) : null}
        {verdict.suggested_action ? (
          <div>
            <dt>Suggested fix</dt>
            <dd>{verdict.suggested_action}</dd>
          </div>
        ) : null}
      </dl>
      {verdict.cause ? (
        <ul className="chat-decision-reasons">
          <li>{verdict.cause}</li>
        </ul>
      ) : null}
    </>
  );
}
