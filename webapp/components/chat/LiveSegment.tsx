import { commands, cycleAddress } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import { useWorkspace } from "@/lib/state/workspace";
import { cx } from "@/lib/cx";
import type { CyclePath } from "@/lib/ids";
import { FailureLine, Said } from "@/components/ui";
import type { ActivityDecision, Said as SaidRow } from "@/lib/api/types";

type DecisionAction = ActivityDecision["actions"][number];

export function LiveSegment({
  notices,
  status,
  listening,
  decision,
}: {
  notices: SaidRow[];
  status: SaidRow | null;
  // Socket open AND the run in flight: the socket stays open against a finished cycle.
  listening: boolean;
  decision: ActivityDecision | null;
}) {
  const cmd = useCommand<string>("decision");
  const { viewed } = useWorkspace();
  const viewedPath = viewed?.path ?? null;

  const now = listening ? (status ?? { tone: "quiet" as const, label: "Listening for activity…", detail: null }) : null;
  if (notices.length === 0 && !now && !decision) return null;

  // The activity is the viewed leaf's, so its decision is addressed there like any command.
  const decide = (path: CyclePath, a: DecisionAction) =>
    void cmd.run(a.label, () => commands[a.kind]({ ...cycleAddress(path), ...a.payload }));
  const busy = cmd.pending !== null || viewedPath === null;

  return (
    <div className="chat-live">
      {notices.map((a) => (
        <div key={a.key} className={cx("chat-activity", `tone-${a.tone}`)}>
          <span className="chat-activity-icon tone-mark" aria-hidden="true" />
          <span className="chat-activity-label">{a.label}</span>
          {a.detail ? <span className="chat-activity-detail">{a.detail}</span> : null}
        </div>
      ))}

      {now ? (
        <div className={cx("chat-activity", `tone-${now.tone}`)} role="status" aria-live="polite">
          <span className="chat-activity-icon tone-mark" aria-hidden="true" />
          <span className="chat-activity-label">{now.label}</span>
          {now.detail ? <span className="chat-activity-detail">{now.detail}</span> : null}
        </div>
      ) : null}

      {decision ? (
        <div className="chat-msg ai chat-decision" role="group" aria-label={decision.title}>
          <div className="chat-decision-title">{decision.title}</div>
          <p className="chat-decision-lead">{decision.lead}</p>
          {decision.facts.length > 0 ? (
            <ul className="chat-decision-verdict">
              {decision.facts.map((f) => (
                <li key={f.key}>
                  <Said tone={f.tone} label={f.label} value={f.value} sub={f.sub} detail={f.detail} />
                </li>
              ))}
            </ul>
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
                onClick={() => viewedPath && decide(viewedPath, a)}
              >
                {cmd.pending === a.label ? "…" : a.label}
              </button>
            ))}
          </div>
          <FailureLine failure={cmd.failure} />
        </div>
      ) : null}
    </div>
  );
}
