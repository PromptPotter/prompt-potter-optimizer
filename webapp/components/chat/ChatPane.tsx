"use client";
import { useEffect, useRef, useState } from "react";
import { useHardSamples } from "@/lib/hard-samples";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { useIngest } from "@/lib/ingest-flow";
import { IngestConversation } from "@/components/ingest/IngestConversation";
import { hasLiveProducer } from "@/lib/run-phase";
import { isSelfOptimization, runSummary } from "@/lib/derivations";
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
  // The selected campaign when it is a durable check-in awaiting authoring, else
  // null. The thread reopens its draft in place — no separate pane, so the hero
  // and the samples stay where they are.
  checkinCampaignId: string | null;
}

// The Chat surface. The pipeline hero is display-only; the live interactive path is
// dataset ingest, rendered by the shared `IngestConversation` (same surface as the
// "New campaign" modal): drop/pick → ask context if missing → one check-in → Start.
// Run status lives on the shell's RemoteControl, and what this chat can DO is the
// composer's Tools popover. Everything above the thread is deliberately MINIATURE —
// the Dashboard is where these same surfaces are read at size.
export function ChatPane({ checkinCampaignId }: Props) {
  // Only the name is read here (the pipeline hero labels itself with it); the roster
  // and its controls go straight to the two panels that draw them.
  const { datasetName } = useHardSamples();
  // Self-sourced live state — the thread's freeze-on-stop edge and the live feed.
  const { dash, isLive } = useDashboard();
  // The live FEED + its gate-decision control follow the viewed LEAF hop (the same
  // hop the dashboard shows) — drilling into an L4 inner campaign tails that inner
  // cycle's own activity, not the outer thread's candidate cards. The gate decision
  // is derived from `dash` (already leaf), so firing it must target the leaf too.
  // Root identity (session, ingest compose) stays on the root exports. Both hops
  // are derived once in the workspace context.
  const { viewedPath, cycleId, leafCampaignId, leafCycleId } = useWorkspace();
  const [samplesOpen, setSamplesOpen] = useState(false);
  const toggleSamples = () => setSamplesOpen((v) => !v);

  // The one authoring thread, shared with the New campaign modal. `composing` is
  // its "the operator is authoring, not watching" flag — it suppresses the bound
  // cycle's live feed so a fresh thread is not drawn over the previous run.
  const { flow: ingest, collection, composing } = useIngest();

  // Reopen a durable check-in's draft straight into the thread. It has no
  // dashboard.json, so this is the authoring surface for it; `reopenCheckin`
  // loads the draft and the last resolver turn from disk. Keyed on the campaign
  // — `ingest` is rebuilt each render but its methods close over stable
  // setState, so the exhaustive-deps lint would over-add it.
  useEffect(() => {
    if (checkinCampaignId) ingest.reopenCheckin(checkinCampaignId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checkinCampaignId]);

  // Freeze a run into the thread on the live→stopped EDGE, so a later `resume`
  // leaves the finished one behind as a log entry instead of re-animating it.
  // Render-phase guarded, and the state being adjusted belongs to a hook this
  // component owns — the sanctioned "adjust state when an input changes" recipe.
  //
  // The identity check is load-bearing, not belt-and-braces: switching from a LIVE
  // cycle to a stopped one walks the same false→null edge, and by then `dash`
  // describes the cycle just navigated TO. Without it that click would file the new
  // cycle's numbers under the old cycle's ending.
  const liveCycleKey = cycleId && isLive ? cycleId : null;
  const [prevLiveCycle, setPrevLiveCycle] = useState(liveCycleKey);
  if (liveCycleKey !== prevLiveCycle) {
    setPrevLiveCycle(liveCycleKey);
    const ended = runSummary(dash);
    if (prevLiveCycle && !liveCycleKey && ended?.cycleId === prevLiveCycle) {
      ingest.pushRunSummary(ended);
    }
  }

  // The chat's curated layer over the cycle event stream (the webapp's first
  // SSE consumer) + the inline gate-decision merge surface. Both bind to the
  // viewed (campaign, cycle); the gate decision is raised from `run_phase`.
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

  // Shared connector view (one provider-level fetch + health poll).
  const cv = useConnector();
  // An L4 self-optimization unit has no cache.json roster — its samples ARE the
  // inner campaigns — so the hard-samples panels point to the inner run instead.
  const selfOpt = isSelfOptimization(cv.backendType);
  const { node: selectedNode, setSelectionForNode } = useSelection();
  // While a campaign is being set up, the connector preview shows the DRAFT's
  // searchpoint (not the prior cycle / origin). Carries through awaiting-context
  // and ready — the two phases that hold a draft.
  const previewDraft =
    ingest.phase.stage === "ready" || ingest.phase.stage === "awaiting-context"
      ? ingest.phase.draft
      : null;
  // Auto-open once per mount as soon as a cycle is bound — saves the operator
  // one click on page reload. The ref guard means that if the user manually
  // closes the drawer and the cycle later changes (or a new cycle is bound),
  // their close preference stays respected instead of being overridden.
  const samplesAutoOpened = useRef(false);
  useEffect(() => {
    if (cycleId && !samplesAutoOpened.current) {
      samplesAutoOpened.current = true;
      setSamplesOpen(true);
    }
  }, [cycleId]);

  return (
    <div className="content chat-content" id="content-chat">
      {/* One anchor on top of the chat: the pipeline hero. The job-bar that used to sit in
          its header row is gone — cycle picker, KPI chips and the status/spend panel are all
          on the shell's RemoteControl now, which renders on every tab rather than this one. */}
      <div className="wf-hero">
        {/* The corner buttons are the zoom axis and belong to the stack, which is the
            only thing that knows how many levels there are to zoom to. Ingest keeps its
            node list always-on: there you configure nodes, not watch them. */}
        <PipelineStack
          datasetName={datasetName}
          samplesOpen={samplesOpen}
          onToggleSamples={toggleSamples}
        />
        {/* The round the hero's node detail reads. Its twin scopes the Dashboard's
            canvas from that card's own toolbar — one control per picture, both
            writing the single `selection.round` axis, so crossing tabs holds the
            round. Without one here the chat could only ever inspect live. */}
        <RoundAxis />
        {/* Either scope. The optimizer rail used to light a node and open nothing,
            because `optimizer`-scoped detail was mounted on the Dashboard alone. */}
        {selectedNode && (
          <NodeDetail
            node={selectedNode}
            draft={previewDraft}
            onClose={() => setSelectionForNode(null)}
          />
        )}
        {/* No pp-self branch: an outer self-optimization cycle has no per-sample
            roster to plot, and pointing at the inner run here was a third copy of
            a `drillInto` the sidebar and the L4 panel rows already offer. */}
        {samplesOpen && !selfOpt && <HardSamplesHeatmap />}
      </div>

      <div className="chat-grid">
        <div className="chat-panel">
          <IngestConversation
            flow={ingest}
            // The entry list, on the landing surface. Withholding it here is what
            // left a visitor with no file of their own and no way in at all.
            origins={collection.kind === "ready" ? collection.origins : undefined}
            datasets={collection.kind === "ready" ? collection.entries : undefined}
            liveSegment={composing ? undefined : liveSegment}
            runCard={
              composing || !cycleId ? undefined : (
                <RunCard sampleOrder={live.sampleOrder} />
              )
            }
          />
        </div>
      </div>
    </div>
  );
}
