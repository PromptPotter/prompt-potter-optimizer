"use client";
// The disabled controls are INTENTIONAL placeholders for the chat-first front door (`docs/specs/chat-foundation.md`):
// exempt from any "hide non-functional controls" sweep and from the no-M-milestone gate.
import { useEffect, useState } from "react";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { useIngest } from "@/lib/ingest-flow";
import { IngestConversation } from "@/components/ingest/IngestConversation";
import { hasLiveProducer } from "@/lib/run-phase";
import { runSummary } from "@/lib/derivations";
import { useCycleEvents } from "@/lib/chat/useCycleEvents";
import { benchPassActivity } from "@/lib/chat/activity";
import { deriveDecision } from "@/lib/chat/decision";
import { LiveSegment } from "@/components/chat/LiveSegment";
import { RunCard } from "@/components/chat/RunCard";

interface Props {
  // The selected campaign when it is a durable check-in awaiting authoring, else null.
  checkinCampaignId: string | null;
  onOpenDashboard: () => void;
}

// The Chat surface: the shared `IngestConversation` thread and nothing above it. The pipeline, the
// samples and a node's detail are the Dashboard's; the run card in the thread is the one snapshot
// of them here, and it links over.
export function ChatPane({ checkinCampaignId, onOpenDashboard }: Props) {
  const { dash } = useDashboard();
  // The feed and its gate decision follow the viewed LEAF hop (an L4 inner campaign tails its own cycle);
  // root identity (session, ingest compose) stays on the root exports.
  const { viewedPath, cycleId, leafCampaignId, leafCycleId } = useWorkspace();

  // `composing` suppresses the bound cycle's live feed so a fresh thread is not drawn over the last run.
  const { flow: ingest, collection, composing } = useIngest();

  // A check-in has no dashboard.json; `reopenCheckin` loads its draft from disk. Keyed on the campaign
  // alone — `ingest`'s methods close over stable setState.
  useEffect(() => {
    if (checkinCampaignId) ingest.reopenCheckin(checkinCampaignId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checkinCampaignId]);

  // Freeze a run into the thread on the live→stopped EDGE; the identity check stops a cycle switch filing
  // the new cycle's numbers under the old one's ending. `hasLiveProducer`, NOT `isLive` (false at the gate).
  const liveCycleKey = cycleId && hasLiveProducer(dash?.run_phase) ? cycleId : null;
  const [prevLiveCycle, setPrevLiveCycle] = useState(liveCycleKey);
  if (liveCycleKey !== prevLiveCycle) {
    setPrevLiveCycle(liveCycleKey);
    const ended = runSummary(dash);
    if (prevLiveCycle && !liveCycleKey && ended?.cycleId === prevLiveCycle) {
      ingest.pushRunSummary(ended);
    }
  }

  const live = useCycleEvents(viewedPath);
  const decision = deriveDecision(dash?.run_phase, dash);
  const liveSegment =
    leafCampaignId && leafCycleId ? (
      <LiveSegment
        campaignId={leafCampaignId}
        cycleId={leafCycleId}
        activity={live.activity}
        progress={(liveCycleKey ? benchPassActivity(dash?.bench_pass) : null) ?? live.progress}
        listening={live.connected && hasLiveProducer(dash?.run_phase)}
        decision={decision}
        hearts={dash?.run_standing?.stalls_left ?? null}
        livesCap={dash?.run_standing?.stalls_left_cap ?? null}
      />
    ) : null;

  return (
    <div className="content chat-content" id="content-chat">
      <div className="chat-grid">
        <div className="chat-panel">
          <IngestConversation
            flow={ingest}
            origins={collection.kind === "ready" ? collection.origins : undefined}
            datasets={collection.kind === "ready" ? collection.entries : undefined}
            liveSegment={composing ? undefined : liveSegment}
            runCard={
              composing || !cycleId ? undefined : (
                <RunCard sampleOrder={live.sampleOrder} onOpenDashboard={onOpenDashboard} />
              )
            }
          />
        </div>
      </div>
    </div>
  );
}
