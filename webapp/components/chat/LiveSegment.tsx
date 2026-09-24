"use client";
import { postOriginGateDecision, type OriginGateDecision } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import { fmtPct0 } from "@/lib/format";
import { cx } from "@/lib/cx";
import { Hearts } from "@/components/ui";
import { HEALTH_CAUSE_LABEL } from "@/lib/derivations";
import type { ActivityItem } from "@/lib/chat/activity";
import type { DecisionItem } from "@/lib/chat/decision";
import type { DegradationHealth } from "@/lib/api/types";

// The live tail of the chat thread, where chat and cycle-trace MERGE: a decision button fires a control
// command that lands on the cycle ledger and re-appears in the feed as "control applied".
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
  /** Socket open AND the run still in flight — the socket stays open against a finished cycle. */
  listening: boolean;
  decision: DecisionItem | null;
  /** Banked lives of the VIEWED cycle; `null` when it isn't in lives mode. */
  hearts?: number | null;
  /** The bank's ceiling. Passed down, never re-derived here. */
  livesCap?: number | null;
}) {
  const cmd = useCommand<OriginGateDecision>("origin-gate");

  const empty = activity.length === 0 && !progress && !decision;
  if (empty && !listening) return null;

  // The decision item clears itself once the poll sees `run_phase` leave `gate`.
  const decide = (d: OriginGateDecision) =>
    void cmd.run(d, () => postOriginGateDecision(campaignId, cycleId, d));
  const busy = cmd.pending !== null;

  const now = progress ?? (empty && listening ? { icon: "·", label: "Listening for activity…", detail: null } : null);

  return (
    <div className="chat-live">
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

      {now ? (
        <div className="chat-activity tone-muted kind-progress" role="status" aria-live="polite">
          <span className="chat-activity-icon" aria-hidden="true">
            {now.icon}
          </span>
          <span className="chat-activity-label">{now.label}</span>
          {now.detail ? <span className="chat-activity-detail">{now.detail}</span> : null}
          {/* The ♥ bank rides this chip, not the round rows: painting it on a finished round misdates it. */}
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
                {cmd.pending === b.decision ? "…" : b.label}
              </button>
            ))}
          </div>
          {cmd.failure ? (
            <p className="chat-decision-err" role="alert">
              {cmd.failure.message}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function GateVerdictView({ verdict }: { verdict: DegradationHealth }) {
  // COVERAGE LEADS: the degraded rate and the advice are computed over disjoint rows.
  const panel = verdict.samples + verdict.not_attempted;
  // Holes never reach the degraded numerator, so the rate shows only where something came back.
  const classifiable = verdict.samples - verdict.hole_count;
  return (
    <>
      <dl className="chat-decision-verdict">
        <div>
          <dt>Cells measured</dt>
          <dd>
            {verdict.samples} of {panel}
            {verdict.not_attempted > 0 ? ` · ${verdict.not_attempted} never sent` : ""}
          </dd>
        </div>
        {verdict.hole_count > 0 ? (
          <div>
            <dt>Returned nothing</dt>
            <dd>{verdict.hole_count}</dd>
          </div>
        ) : null}
        {classifiable > 0 ? (
          <div>
            <dt>Degraded rate</dt>
            <dd>
              {fmtPct0(verdict.degraded_rate)} · {verdict.structural_count} structural /{" "}
              {verdict.transient_count} transient
            </dd>
          </div>
        ) : null}
        {verdict.dominant_node ? (
          <div>
            <dt>Worst node</dt>
            <dd>{verdict.dominant_node}</dd>
          </div>
        ) : null}
        {verdict.last_error ? (
          <div>
            <dt>Last error</dt>
            <dd className="chat-decision-error">{verdict.last_error}</dd>
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
          <li>{HEALTH_CAUSE_LABEL[verdict.cause]}</li>
        </ul>
      ) : null}
    </>
  );
}
