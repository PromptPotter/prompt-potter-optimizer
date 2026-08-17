"use client";
import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { DatasetItem, HardSampleOrder, HardSamplesScope, SampleSeries } from "@/lib/api";
import type { SeriesTotals } from "@/lib/hooks/useDatasetPreview";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { useIngestFlow } from "@/lib/hooks/useIngestFlow";
import { IngestConversation } from "@/components/ingest/IngestConversation";
import type { OnMinted } from "@/components/ingest/types";
import { isInFlight } from "@/lib/run-phase";
import { isSelfOptimization, runSummary } from "@/lib/derivations";
import { Switch } from "@/components/ui";
import { HardSamplesHeatmap } from "@/components/dashboard/samples/HardSamplesHeatmap";
import { BackendNodeDetail } from "@/components/dashboard/pipeline/BackendNodeDetail";
import { PipelineStack } from "@/components/dashboard/pipeline/PipelineStack";
import { useConnector } from "@/lib/hooks/useConnector";
import { useSelection } from "@/lib/SelectionContext";
import { useCycleEvents } from "@/lib/chat/useCycleEvents";
import { deriveDecision } from "@/lib/chat/decision";
import { LiveSegment } from "@/components/chat/LiveSegment";
import { RunCard } from "@/components/chat/RunCard";
import { RunMasthead } from "@/components/shell/RunMasthead";

// Fireflies orbiting the wand frame. Offset, phase and size are all that differ
// between them, so they are data rather than eight near-identical CSS rules.
type SparkleStyle = CSSProperties & Record<`--sparkle-${string}`, string>;
const SPARKLES: SparkleStyle[] = [
  { top: "-3px", left: "18%", "--sparkle-delay": "0s", "--sparkle-dur": "2.1s", "--sparkle-size": "4px" },
  { top: "30%", right: "-3px", "--sparkle-delay": ".7s", "--sparkle-dur": "3.3s", "--sparkle-size": "4px" },
  { bottom: "-3px", right: "25%", "--sparkle-delay": "1.5s", "--sparkle-dur": "2.7s", "--sparkle-size": "4px" },
  { bottom: "40%", left: "-3px", "--sparkle-delay": ".4s", "--sparkle-dur": "3.6s", "--sparkle-size": "3px" },
  { top: "-2px", right: "30%", "--sparkle-delay": "2.0s", "--sparkle-dur": "2.4s", "--sparkle-size": "3px" },
  { bottom: "-2px", left: "38%", "--sparkle-delay": "1.2s", "--sparkle-dur": "3.0s", "--sparkle-size": "3px" },
  { top: "60%", right: "-3px", "--sparkle-delay": "1.8s", "--sparkle-dur": "3.5s", "--sparkle-size": "3px" },
  { top: "20%", left: "-3px", "--sparkle-delay": ".9s", "--sparkle-dur": "2.8s", "--sparkle-size": "3px" },
];

interface Props {
  datasetName: string | null;
  datasetItems: DatasetItem[];
  datasetMeasuredCount: number;
  datasetUnmeasuredCount: number;
  datasetSplitTest: number | null;
  // Passed to BOTH consumers, so the pane and the run card cannot name different orders.
  datasetOrder: HardSampleOrder | null;
  archivePerSample: Map<number, SampleSeries>;
  datasetTotals: SeriesTotals | null;
  // True while the displayed dataset slice is from a prior (unit, scope) and
  // a fresh fetch is in flight — lets the table dim instead of blanking.
  datasetStale: boolean;
  datasetError: string | null;
  hardSamplesScope: HardSamplesScope;
  onHardSamplesScopeChange: (s: HardSamplesScope) => void;
  hardSampleOrder: HardSampleOrder | null;
  onHardSampleOrderChange: (o: HardSampleOrder) => void;
  // Bumped by the shell's "New campaign" button while this tab is in view —
  // each change resets the thread to its empty first-run state (compose mode),
  // suppressing the bound cycle's live feed until a fresh campaign is minted.
  newCampaignTick: number;
  // Fired when the inline ingest flow mints a campaign — AppShell selects the
  // new cycle. The whole drop → context → check-in → Start path runs inline
  // here via the shared `IngestConversation`; nothing is handed off to a modal.
  onMinted: OnMinted;
}

// The Chat surface. The pipeline hero + settings card are display-only. The live
// interactive path is dataset ingest, rendered by the shared `IngestConversation` (same
// surface as the "New campaign" modal): drop/pick → ask context if missing → one check-in
// → Start. Run status and control live on the shell's RemoteControl, not here.
export function ChatPane({
  datasetName,
  datasetItems,
  datasetMeasuredCount,
  datasetUnmeasuredCount,
  datasetSplitTest,
  datasetOrder,
  archivePerSample,
  datasetTotals,
  datasetStale,
  datasetError,
  hardSamplesScope,
  onHardSamplesScopeChange,
  hardSampleOrder,
  onHardSampleOrderChange,
  newCampaignTick,
  onMinted,
}: Props) {
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

  // Compose mode — entered by the shell's "New campaign" button (newCampaignTick).
  // While composing, the bound cycle's live feed is suppressed so the thread shows
  // its empty first-run state; minting a campaign clears it.
  const [composing, setComposing] = useState(false);

  // The dataset-ingest conversation, run inline on the chat tab. Same state
  // machine + view the "New campaign" modal uses (one path, one check-in call).
  const ingest = useIngestFlow({
    onMint: (sel) => {
      setComposing(false);
      onMinted(sel);
    },
  });

  // Reset the thread to its empty first-run state on each "New campaign" hit.
  // Render-phase guarded (webapp/CLAUDE.md "State reset on prop change") so the
  // cleared thread commits with the same frame — `ingest.reset()` clears this
  // component's own hook state, the same as setting local state here.
  const [prevNewCampaignTick, setPrevNewCampaignTick] = useState(newCampaignTick);
  if (newCampaignTick !== prevNewCampaignTick) {
    setPrevNewCampaignTick(newCampaignTick);
    setComposing(true);
    ingest.reset();
  }

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
        listening={live.connected && isInFlight(dash?.run_phase)}
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
  // The scope the click was recorded with — NOT a name test. The ids are not a
  // disjoint namespace: pp-self's target pipeline declares `l1_generate` &
  // friends, the optimizer's own node names.
  const showBackendDetail = selectedNode?.scope === "target";
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
      <RunMasthead />
      {/* One anchor on top of the chat: the pipeline hero. The job-bar that used to sit in
          its header row is gone — cycle picker, KPI chips and the status/spend panel are all
          on the shell's RemoteControl now, which renders on every tab rather than this one. */}
      <div className="wf-hero">
        {/* One frame over the nesting this campaign sits in — the corner buttons are the
            zoom axis and belong to the stack, which is the only thing that knows how many
            levels there are to zoom to. Ingest keeps its node list always-on: there you
            configure nodes, not watch them. */}
        <div className="pipeline-frame">
          <PipelineStack
            datasetName={datasetName}
            samplesOpen={samplesOpen}
            onToggleSamples={toggleSamples}
          />
        </div>
        {showBackendDetail && (
          <BackendNodeDetail
            draft={previewDraft}
            onClose={() => setSelectionForNode(null)}
          />
        )}
        {/* No pp-self branch: an outer self-optimization cycle has no per-sample
            roster to plot, and pointing at the inner run here was a third copy of
            a `drillInto` the sidebar and the L4 panel rows already offer. */}
        {samplesOpen && !selfOpt && (
          <HardSamplesHeatmap
            datasetName={datasetName}
            datasetItems={datasetItems}
            datasetMeasuredCount={datasetMeasuredCount}
            datasetUnmeasuredCount={datasetUnmeasuredCount}
            datasetSplitTest={datasetSplitTest}
            datasetOrder={datasetOrder}
            archivePerSample={archivePerSample}
            datasetTotals={datasetTotals}
            datasetStale={datasetStale}
            datasetError={datasetError}
            hardSamplesScope={hardSamplesScope}
            onHardSamplesScopeChange={onHardSamplesScopeChange}
            hardSampleOrder={hardSampleOrder}
            onHardSampleOrderChange={onHardSampleOrderChange}
          />
        )}
      </div>

      <div className="chat-grid">
        <div className="chat-panel">
          <div className="chat-panel-header">
            <div className="chat-panel-title">New Chat</div>
          </div>
          <IngestConversation
            flow={ingest}
            variant="inline"
            liveSegment={composing ? undefined : liveSegment}
            runCard={
              composing || !cycleId ? undefined : (
                <RunCard
                  datasetName={datasetName}
                  datasetItems={datasetItems}
                  datasetMeasuredCount={datasetMeasuredCount}
                  datasetUnmeasuredCount={datasetUnmeasuredCount}
                  datasetSplitTest={datasetSplitTest}
                  datasetOrder={datasetOrder}
                  archivePerSample={archivePerSample}
                  datasetTotals={datasetTotals}
                  datasetStale={datasetStale}
                  datasetError={datasetError}
                  hardSamplesScope={hardSamplesScope}
                  onHardSamplesScopeChange={onHardSamplesScopeChange}
                  hardSampleOrder={hardSampleOrder}
                  onHardSampleOrderChange={onHardSampleOrderChange}
                  sampleOrder={live.sampleOrder}
                />
              )
            }
          />
        </div>

        <div className="chat-settings">
          <div className="chat-settings-card">
            <div className="chat-settings-title">Settings</div>
            <div className="toggle-row">
              <div className="row-text">
                <span className="row-icon">
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                    <circle cx="8" cy="8" r="6" opacity=".3" />
                    <path d="M8 4v4l3 2" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
                  </svg>
                </span>
                <div className="row-body"><div className="name">Extended thinking<span className="soon-tag">Soon</span></div></div>
              </div>
              <Switch checked={false} locked label="Extended thinking" />
            </div>
            <div className="toggle-row">
              <div className="row-text">
                <span className="row-icon">
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                    <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.2" fill="none" />
                    <path d="M2 8h12M8 2c2 1.8 2 10.2 0 12M8 2c-2 1.8-2 10.2 0 12" stroke="currentColor" strokeWidth="1.1" fill="none" />
                  </svg>
                </span>
                <div className="row-body"><div className="name">Web search<span className="soon-tag">Soon</span></div></div>
              </div>
              <Switch checked={false} locked label="Web search" />
            </div>
            <div className="toggle-row">
              <div className="row-text">
                <span className="row-icon">
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M5 4 1.5 8 5 12" />
                    <path d="M11 4l3.5 4L11 12" />
                    <path d="M9.5 3.5l-3 9" opacity=".6" />
                  </svg>
                </span>
                <div className="row-body"><div className="name">Code execution<span className="soon-tag">Soon</span></div></div>
              </div>
              <Switch checked={false} locked label="Code execution" />
            </div>
            <div className="row-separator" />
            <div className="toggle-row wand-row">
              <div className="row-text">
                <span className="row-icon">
                  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M2.5 13.5 10 6" />
                    <path d="m12 1.5.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7Z" fill="currentColor" />
                    <path d="m5 2.4.4 1.1 1.1.4-1.1.4L5 5.4l-.4-1.1-1.1-.4 1.1-.4Z" fill="currentColor" opacity=".7" />
                  </svg>
                </span>
                <div className="row-body">
                  <div className="name">Optimize prompt while using</div>
                  <div className="desc">Quietly evolves parameters across your project</div>
                </div>
              </div>
              {/* No switch and no SOON tag: this is what the engine DOES, not a feature to
                  turn on, so a control here would offer a choice the product does not have. */}
              {SPARKLES.map((style, i) => (
                <span key={i} className="sparkle" style={style} />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
