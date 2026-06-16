"use client";
import { useEffect, useRef, useState } from "react";
import type { DashboardSnapshot } from "@/lib/poll";
import type { DatasetItem, HardSamplesScope, MeasurementDot } from "@/lib/api";
import { useIngestFlow } from "@/lib/hooks/useIngestFlow";
import { IngestConversation } from "@/components/ingest/IngestConversation";
import type { OnMinted } from "@/components/ingest/types";
import { TERMS, targetNodeIds } from "@/lib/terms";
import { headlineStats, readSpend } from "@/lib/derivations";
import { fmtText, fmtDuration, fmtUsd, fmtTokens } from "@/lib/format";
import { Switch } from "@/components/ui";
import { FitnessPanel } from "@/components/whatif/FitnessPanel";
import { HardSamplesHeatmap } from "@/components/dashboard/samples/HardSamplesHeatmap";
import { CyclePicker } from "@/components/shell/CyclePicker";
import { TargetPipelineHero } from "@/components/dashboard/pipeline/TargetPipelineHero";
import { BackendNodeDetail } from "@/components/dashboard/pipeline/BackendNodeDetail";
import { PipelineNodeList } from "@/components/dashboard/pipeline/PipelineNodeList";
import { SpendBudgetControl } from "@/components/dashboard/control/SpendBudgetControl";
import { useConnector } from "@/lib/hooks/useConnector";
import { useSelection } from "@/lib/SelectionContext";

interface Props {
  campaignId: string | null;
  cycleId: string | null;
  sessionId: string | null;
  datasetTitle: string | null;
  dash: DashboardSnapshot | null;
  // Freshness gate — forwarded to the hard-samples heatmap.
  isLive: boolean;
  dashRound: number | null;
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
  campaignId,
  cycleId,
  sessionId,
  datasetTitle,
  dash,
  isLive,
  dashRound,
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
  onMinted,
}: Props) {
  const [jobOpen, setJobOpen] = useState(false);
  const [wandOn, setWandOn] = useState(true);
  const [samplesOpen, setSamplesOpen] = useState(false);
  const toggleSamples = () => setSamplesOpen((v) => !v);

  // The dataset-ingest conversation, run inline on the chat tab. Same state
  // machine + view the "New campaign" modal uses (one path, one check-in call).
  const ingest = useIngestFlow({ onMint: (sel) => onMinted(sel) });

  // Shared connector view (one provider-level fetch + health poll).
  const cv = useConnector();
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
  // The collapsed toggle's BUDGET chip: spent / limit. USD when the rate is
  // known and a USD ceiling is armed; otherwise the token ceiling (the
  // free-backend case, where USD only sees optimizer cost).
  const budgetChip = (() => {
    if (rateKnown && budgetUsd != null)
      return `${usedUsd != null ? fmtUsd(usedUsd) : "$0"} / ${fmtUsd(budgetUsd)}`;
    if (budgetTokens != null) return `${fmtTokens(totalTokens)} / ${fmtTokens(budgetTokens)}`;
    if (rateKnown && usedUsd != null) return fmtUsd(usedUsd);
    return "—";
  })();
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
      {cycleId ? (
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
              <span className="chip" title={TERMS.newjob_bar_budget}>
                <span className="chip-lbl">Budget</span> <strong>{budgetChip}</strong>
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
              <FitnessPanel dash={dash} dashRound={dashRound} cycleId={cycleId} />
            </div>
            <div className="job-section" title={TERMS.newjob_bar_adjust}>
              <div className="section-title">Finishing criteria</div>
              <SpendBudgetControl
                campaignId={campaignId}
                cycleId={cycleId}
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

      <div className="wf-hero">
        <TargetPipelineHero samplesOpen={samplesOpen} onToggle={toggleSamples} cv={cv} />
        <PipelineNodeList cv={cv} />
        {showBackendDetail && (
          <BackendNodeDetail
            cv={cv}
            dash={dash}
            draft={previewDraft}
            onClose={() => setSelectionForNode(null)}
          />
        )}
        {samplesOpen && (
          <HardSamplesHeatmap
            campaignId={campaignId}
            cycleId={cycleId}
            dash={dash}
            isLive={isLive}
            dashRound={dashRound}
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
        )}
      </div>

      <div className="chat-grid">
        <div className="chat-panel">
          <div className="chat-panel-header">
            <div className="chat-panel-title">New Chat</div>
          </div>
          <IngestConversation flow={ingest} variant="inline" />
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
