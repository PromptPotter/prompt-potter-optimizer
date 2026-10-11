"use client";
import { postCommand } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import { cx } from "@/lib/cx";
import { Hearts } from "@/components/ui";
import type {
  ActivityDecision,
  ActivityItem,
  DecisionAction,
  LivesReading,
} from "@/lib/api/types";

export function LiveSegment({
  notices,
  status,
  listening,
  decision,
  lives,
}: {
  notices: ActivityItem[];
  status: ActivityItem | null;
  // Socket open AND the run in flight: the socket stays open against a finished cycle.
  listening: boolean;
  decision: ActivityDecision | null;
  lives?: LivesReading | null;
}) {
  const cmd = useCommand<string>("decision");

  const now = listening ? (status ?? { icon: "·", label: "Listening for activity…", detail: null }) : null;
  if (notices.length === 0 && !now && !decision) return null;

  const decide = (a: DecisionAction) => void cmd.run(a.label, () => postCommand(a.kind, a.payload));
  const busy = cmd.pending !== null;

  return (
    <div className="chat-live">
      {notices.map((a) => (
        <div
          key={a.id}
          className={cx("chat-activity", `tone-${a.tone}`, `kind-${a.kind}`)}
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
          {lives && <Hearts lives={lives} className="chat-activity-hearts" />}
        </div>
      ) : null}

      {decision ? (
        <div className="chat-msg ai chat-decision" role="group" aria-label={decision.title}>
          <div className="chat-decision-title">{decision.title}</div>
          <p className="chat-decision-lead">{decision.lead}</p>
          {decision.facts.length > 0 ? (
            <dl className="chat-decision-verdict">
              {decision.facts.map((f) => (
                <div key={f.label}>
                  <dt>{f.label}</dt>
                  <dd className={cx(f.tone === "bad" && "chat-decision-error")}>{f.value}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          {decision.reasons.length > 0 ? (
            <ul className="chat-decision-reasons">
              {decision.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          ) : null}
          <div className="chat-decision-actions">
            {decision.actions.map((a) => (
              <button
                key={a.label}
                type="button"
                className={cx("chat-decision-btn", `btn-${a.variant}`)}
                disabled={busy}
                onClick={() => decide(a)}
              >
                {cmd.pending === a.label ? "…" : a.label}
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
