"use client";
import { useEffect, useRef, useState } from "react";
import type { DatasetItem, HardSamplesScope, MeasurementDot } from "@/lib/api";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { useIngestFlow } from "@/lib/hooks/useIngestFlow";
import { IngestConversation } from "@/components/ingest/IngestConversation";
import type { OnMinted } from "@/components/ingest/types";
import { TERMS, targetNodeIds } from "@/lib/terms";
import { headlineStats, isSelfOptimization, readSpend } from "@/lib/derivations";
import { fmtText, fmtDuration, fmtUsd, fmtTokens } from "@/lib/format";
import { Switch } from "@/components/ui";
import { FitnessPanel } from "@/components/whatif/FitnessPanel";
import { HardSamplesHeatmap } from "@/components/dashboard/samples/HardSamplesHeatmap";
import { SelfOptSamplesPointer } from "@/components/dashboard/samples/SelfOptSamplesPointer";
import { CyclePicker } from "@/components/shell/CyclePicker";
import { TargetPipelineHero } from "@/components/dashboard/pipeline/TargetPipelineHero";
import { BackendNodeDetail } from "@/components/dashboard/pipeline/BackendNodeDetail";
import { PipelineNodeList } from "@/components/dashboard/pipeline/PipelineNodeList";
import { SpendBudgetControl } from "@/components/dashboard/control/SpendBudgetControl";
import { useConnector } from "@/lib/hooks/useConnector";
import { useSelection } from "@/lib/SelectionContext";
import { useCycleEvents } from "@/lib/chat/useCycleEvents";
import { deriveDecision } from "@/lib/chat/decision";
import { LiveSegment } from "@/components/chat/LiveSegment";

interface Props {
  datasetTitle: string | null;
  cycleStartedAt: string | null;
  datasetName: string | null;
  datasetItems: DatasetItem[];
  datasetMeasuredCount: number;
  datasetUnmeasuredCount: number;
  datasetSplitTest: number | null;
  archivePerSample: Map<number, MeasurementDot[]>;
  // True while the displayed dataset slice is from a prior (unit, scope) and
  // a fresh fetch is in flight — lets the table dim instead of blanking.
  datasetStale: boolean;
  hardSamplesScope: HardSamplesScope;
  onHardSamplesScopeChange: (s: HardSamplesScope) => void;
  // Bumped by the shell's "New campaign" button while this tab is in view —
  // each change resets the thread to its empty first-run state (compose mode),
  // suppressing the bound cycle's live feed until a fresh campaign is minted.
  newCampaignTick: number;
  // Fired when the inline ingest flow mints a campaign — AppShell selects the
  // new cycle. The whole drop → context → check-in → Start path runs inline
  // here via the shared `IngestConversation`; nothing is handed off to a modal.
  onMinted: OnMinted;
}

// ETA to budget — burn rate = used / cycle_age, ETA = remaining_budget / burn.
// A plain module-level helper (not a component/hook) so the wallclock read is
// allowed; returns "—" until spend is wired or when budget is uncapped / spent.
function etaToBudget(
  usedUsd: number | null,
  budgetUsd: number | null,
  cycleStartedAt: string | null,
): string {
  if (usedUsd == null || budgetUsd == null || !cycleStartedAt) return "—";
  const startedMs = Date.parse(cycleStartedAt);
  if (!Number.isFinite(startedMs)) return "—";
  const ageSec = (Date.now() - startedMs) / 1000;
  if (ageSec <= 0 || usedUsd <= 0) return "—";
  if (usedUsd >= budgetUsd) return "spent";
  const burn = usedUsd / ageSec; // $/sec
  const remainingSec = (budgetUsd - usedUsd) / burn;
  return fmtDuration(remainingSec);
}

// The Chat surface. The job-bar + pipeline hero + settings card are display-only
// (control plane lands in M12). The live interactive path is dataset ingest,
// rendered by the shared `IngestConversation` (same surface as the "New
// campaign" modal): drop/pick → ask context if missing → one check-in → Start.
export function ChatPane({
  datasetTitle,
  cycleStartedAt,
  datasetName,
  datasetItems,
  datasetMeasuredCount,
  datasetUnmeasuredCount,
  datasetSplitTest,
  archivePerSample,
  datasetStale,
  hardSamplesScope,
  onHardSamplesScopeChange,
  newCampaignTick,
  onMinted,
}: Props) {
  // Self-sourced live state + identity for the job bar + spend chips.
  const { dash } = useDashboard();
  const { campaignId, cycleId, sessionId } = useWorkspace();
  const [jobOpen, setJobOpen] = useState(false);
  const [wandOn, setWandOn] = useState(true);
  const [samplesOpen, setSamplesOpen] = useState(false);
  const toggleSamples = () => setSamplesOpen((v) => !v);

  // Compose mode — entered by the shell's "New campaign" button (newCampaignTick).
  // While composing, the bound cycle's live feed + job bar are suppressed so the
  // thread shows its empty first-run state; minting a campaign clears it.
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

  // The chat's curated layer over the cycle event stream (the webapp's first
  // SSE consumer) + the inline gate-decision merge surface. Both bind to the
  // viewed (campaign, cycle); the gate decision is raised from `run_phase`.
  const live = useCycleEvents(campaignId, cycleId);
  const decision = deriveDecision(dash?.run_phase, dash);
  const liveSegment =
    campaignId && cycleId ? (
      <LiveSegment
        campaignId={campaignId}
        cycleId={cycleId}
        activity={live.activity}
        progress={live.progress}
        connected={live.connected}
        decision={decision}
      />
    ) : null;

  // Shared connector view (one provider-level fetch + health poll).
  const cv = useConnector();
  // An L4 self-optimization unit has no cache.json roster — its samples ARE the
  // inner campaigns — so the hard-samples panels point to the inner run instead.
  const selfOpt = isSelfOptimization(cv.backendType);
  const { node: selectedNode, setSelectionForNode } = useSelection();
  // Target-node ids are disjoint from the optimizer canvas (membership-gate);
  // the demo/preview hero with no view exposes the synthetic "llm" chip id.
  const showBackendDetail =
    selectedNode != null && targetNodeIds(cv.view).includes(selectedNode);
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

  // Headline KPIs + spend — both read through the shared derivations so the
  // chat job-bar can't disagree with the console telemetry strip.
  const { best, origin, delta } = headlineStats(dash);
  const bestPctOnly = best != null ? `${(best * 100).toFixed(0)}%` : "—";
  const originPct = origin != null ? `${(origin * 100).toFixed(0)}%` : null;

  const {
    backendUsd,
    loopUsd,
    usedUsd,
    budgetUsd,
    budgetTokens,
    rateKnown,
    backendTokens,
    loopTokens,
    totalTokens,
  } = readSpend(dash);
  const deltaPerSpend =
    delta != null && usedUsd != null && usedUsd > 0 ? delta / usedUsd : null;
  const effChip =
    deltaPerSpend != null ? `${(deltaPerSpend * 100).toFixed(2)} pp/$` : "—";

  // ETA to budget — pure client-side derivation. The wallclock read lives in
  // the module-level `etaToBudget` helper (not the component body) so React's
  // purity rule stays satisfied; the re-render cadence is driven by dash polling
  // so the value never goes stale visibly.
  const etaChip = etaToBudget(usedUsd, budgetUsd, cycleStartedAt);

  return (
    <div className="content chat-content" id="content-chat">
      {/* One anchor on top of the chat: the pipeline hero, with the job-bar
          (cycle picker + KPI chips + status/spend dropdown) folded in as its
          header row — not a separate strip stacked above it. */}
      <div className="wf-hero">
      {cycleId && !composing ? (
      <div className={`chat-job-bar${jobOpen ? " open" : ""}`}>
        <div className="chat-job-head">
          <svg className="grid" width="14" height="14" viewBox="0 0 14 14" fill="currentColor" aria-hidden="true">
            <rect x="1" y="1" width="5" height="5" rx="1" />
            <rect x="8" y="1" width="5" height="5" rx="1" opacity=".55" />
            <rect x="1" y="8" width="5" height="5" rx="1" opacity=".55" />
            <rect x="8" y="8" width="5" height="5" rx="1" opacity=".35" />
          </svg>
          <CyclePicker variant="standalone" />
          <button
            type="button"
            className="chat-job-toggle"
            aria-expanded={jobOpen}
            onClick={() => setJobOpen((v) => !v)}
            aria-label="Job status and configuration"
          >
            <span className="chip-row">
              <span className="chip" title={TERMS.newjob_bar_best}>
                <span className="chip-lbl">Best</span> <strong>{bestPctOnly}</strong>
                {originPct && <span className="chip-origin"> / {originPct}</span>}
              </span>
              <span className="chip" title={TERMS.newjob_bar_eta}>
                <span className="chip-lbl">ETA</span> <strong>{etaChip}</strong>
              </span>
              <span className="chip" title={TERMS.newjob_bar_eff}>
                <span className="chip-lbl">Δ/$</span> <strong>{effChip}</strong>
              </span>
            </span>
            <svg className="chev" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="m3 4.5 3 3 3-3" />
            </svg>
          </button>
        </div>
        {jobOpen && (
          <div className="chat-job-dropdown" role="region" aria-label="Job status and configuration">
            <div className="job-section">
              <div className="section-title">Identity</div>
              <div className="row"><span className="lbl">Unit</span><span className="val">{fmtText(cycleId)}</span></div>
              <div className="row"><span className="lbl">Session</span><span className="val">{fmtText(sessionId)}</span></div>
              <div className="row"><span className="lbl">Project</span><span className="val">{fmtText(datasetTitle)}</span></div>
              <div className="row"><span className="lbl">Updated</span><span className="val">{fmtText(dash?.wallclock_serialized_at)}</span></div>
              <div className="section-title" style={{ marginTop: 12 }}>Spend</div>
              <div className="row"><span className="lbl">Backend</span><span className="val">{rateKnown ? fmtUsd(backendUsd) : `${backendTokens} tok`}</span></div>
              <div className="row"><span className="lbl">Loop</span><span className="val">{rateKnown ? fmtUsd(loopUsd) : `${loopTokens} tok`}</span></div>
              <div className="row"><span className="lbl">Total</span><span className="val">{usedUsd != null ? fmtUsd(usedUsd) : "—"}</span></div>
              <div className="row"><span className="lbl">Tokens</span><span className="val">{fmtTokens(totalTokens)}</span></div>
              <div className="row"><span className="lbl">Spend cap</span><span className="val">{budgetUsd != null ? fmtUsd(budgetUsd) : "Uncapped"}</span></div>
              <div className="row"><span className="lbl">Token cap</span><span className="val">{budgetTokens != null ? fmtTokens(budgetTokens) : "Uncapped"}</span></div>
            </div>
            <div className="job-whatif">
              <FitnessPanel />
            </div>
            <div className="job-section" title={TERMS.newjob_bar_adjust}>
              <div className="section-title">Finishing criteria</div>
              <SpendBudgetControl
                currentBudgetUsd={budgetUsd}
                currentBudgetTokens={budgetTokens}
                usedUsd={usedUsd}
                usedTokens={totalTokens}
              />
            </div>
          </div>
        )}
      </div>
      ) : null}

        <TargetPipelineHero samplesOpen={samplesOpen} onToggle={toggleSamples} />
        <PipelineNodeList />
        {showBackendDetail && (
          <BackendNodeDetail
            draft={previewDraft}
            onClose={() => setSelectionForNode(null)}
          />
        )}
        {samplesOpen &&
          (selfOpt ? (
            <SelfOptSamplesPointer />
          ) : (
            <HardSamplesHeatmap
              datasetName={datasetName}
              datasetItems={datasetItems}
              datasetMeasuredCount={datasetMeasuredCount}
              datasetUnmeasuredCount={datasetUnmeasuredCount}
              datasetSplitTest={datasetSplitTest}
              archivePerSample={archivePerSample}
              datasetStale={datasetStale}
              hardSamplesScope={hardSamplesScope}
              onHardSamplesScopeChange={onHardSamplesScopeChange}
            />
          ))}
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
                  <div className="name">Optimize prompt while using<span className="beta-tag">Beta</span></div>
                  <div className="desc">Quietly evolves parameters across your project</div>
                </div>
              </div>
              <Switch
                checked={wandOn}
                onChange={() => setWandOn((v) => !v)}
                label="Optimize prompt while using"
              />
              <span className="sparkle s1" /><span className="sparkle s2" /><span className="sparkle s3" /><span className="sparkle s4" />
              <span className="sparkle s5" /><span className="sparkle s6" /><span className="sparkle s7" /><span className="sparkle s8" />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
