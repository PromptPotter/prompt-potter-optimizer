"use client";
// The disabled controls are INTENTIONAL placeholders for the chat-first front door (`docs/specs/chat-foundation.md`):
// exempt from any "hide non-functional controls" sweep and from the no-M-milestone gate.
import { useEffect, useMemo, useRef, useState } from "react";
import { useHardSamples } from "@/lib/hard-samples";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { useIngest } from "@/lib/ingest-flow";
import { IngestConversation } from "@/components/ingest/IngestConversation";
import { hasLiveProducer } from "@/lib/run-phase";
import { draftForCampaign, isSelfOptimization, runSummary } from "@/lib/derivations";
import { HardSamplesHeatmap } from "@/components/dashboard/samples/HardSamplesHeatmap";
import { NodeDetail } from "@/components/shell/node-surface/NodeDetail";
import { PipelineStack } from "@/components/dashboard/pipeline/PipelineStack";
import { RoundAxis } from "@/components/workflow";
import { useConnector } from "@/lib/hooks/useConnector";
import { useSelection } from "@/lib/SelectionContext";
import { useCycleEvents } from "@/lib/chat/useCycleEvents";
import { deriveDecision } from "@/lib/chat/decision";
import { LiveSegment } from "@/components/chat/LiveSegment";
import { RunCard } from "@/components/chat/RunCard";

interface Props {
  // The selected campaign when it is a durable check-in awaiting authoring, else null.
  checkinCampaignId: string | null;
  onOpenDashboard: () => void;
}

// The Chat surface: a display-only pipeline hero over the shared `IngestConversation` thread. Everything
// above the thread is deliberately MINIATURE — the Dashboard reads the same surfaces at size.
export function ChatPane({ checkinCampaignId, onOpenDashboard }: Props) {
  const { datasetName } = useHardSamples();
  const { dash } = useDashboard();
  // The feed and its gate decision follow the viewed LEAF hop (an L4 inner campaign tails its own cycle);
  // root identity (session, ingest compose) stays on the root exports.
  const { viewedPath, cycleId, leafCampaignId, leafCycleId } = useWorkspace();
  const [samplesOpen, setSamplesOpen] = useState(false);
  const toggleSamples = () => setSamplesOpen((v) => !v);

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
        progress={live.progress}
        listening={live.connected && hasLiveProducer(dash?.run_phase)}
        decision={decision}
        hearts={dash?.hearts ?? null}
        livesCap={dash?.run_limits?.lives_cap ?? null}
      />
    ) : null;

  const cv = useConnector();
  // An L4 unit has no cache.json roster — its samples ARE the inner campaigns.
  const selfOpt = isSelfOptimization(cv.backendType);
  const { node: selectedNode, setSelectionForNode } = useSelection();
  // A campaign being set up previews the DRAFT's searchpoint, only for the campaign that draft is:
  // the ingest thread outlives a sidebar selection.
  const previewDraft = draftForCampaign(
    ingest.phase.stage === "ready" || ingest.phase.stage === "awaiting-context"
      ? ingest.phase.draft
      : null,
    leafCampaignId,
  );
  // The draft's documents, not the wire, so config is read only where the served resolution answered.
  const authoring = useMemo(
    () =>
      previewDraft
        ? {
            overlay: previewDraft.pipeline_overlay,
            promptFields: previewDraft.origin_prompt_fields,
          }
        : undefined,
    [previewDraft],
  );
  // Auto-open once per mount; the ref keeps a manual close respected across cycle changes.
  const samplesAutoOpened = useRef(false);
  useEffect(() => {
    if (cycleId && !samplesAutoOpened.current) {
      samplesAutoOpened.current = true;
      setSamplesOpen(true);
    }
  }, [cycleId]);

  return (
    <div className="content chat-content" id="content-chat">
      <div className="wf-hero">
        {/* The corner zoom buttons belong to the stack, the only thing that knows the level count. */}
        <PipelineStack
          datasetName={datasetName}
          samplesOpen={samplesOpen}
          onToggleSamples={toggleSamples}
        />
        {/* Its twin is on the Dashboard canvas toolbar; both write the one `selection.round` axis. */}
        <RoundAxis />
        {selectedNode && (
          <NodeDetail
            node={selectedNode}
            authoring={authoring}
            onClose={() => setSelectionForNode(null)}
          />
        )}
        {samplesOpen && !selfOpt && <HardSamplesHeatmap />}
      </div>

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
