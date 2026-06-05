"use client";
import { useEffect, useRef, useState } from "react";
import type { DashboardSnapshot } from "@/lib/poll";
import type {
  DatasetItem,
  DraftCampaignWire,
  HardSamplesScope,
  MeasurementDot,
  OriginLastResolution,
} from "@/lib/api";
import {
  IngestApiError,
  postDraftFromDataset,
  postIngestDataset,
  postReplaceDataset,
  postResolveOrigin,
} from "@/lib/api";
import { plainLanguageRecap } from "@/lib/origin-readiness";
import { cx } from "@/lib/cx";
import { TERMS } from "@/lib/terms";
import { headlineStats } from "@/lib/derivations/headline-stats";
import { readSpend } from "@/lib/derivations/spend";
import { fmtText, fmtDuration, fmtUsd } from "@/lib/format";
import { Switch } from "@/components/ui/Switch";
import { ChatFileChip } from "@/components/chat/ChatFileChip";
import { CheckinLoadingWindow } from "@/components/ingest/CheckinLoadingWindow";
import { FitnessPanel } from "@/components/whatif/FitnessPanel";
import { HardSamplesHeatmap } from "@/components/dashboard/samples/HardSamplesHeatmap";
import { CyclePicker } from "@/components/shell/CyclePicker";
import { TargetPipelineHero } from "@/components/dashboard/pipeline/TargetPipelineHero";
import { BackendNodeDetail } from "@/components/dashboard/pipeline/BackendNodeDetail";
import { SpendBudgetControl } from "@/components/dashboard/control/SpendBudgetControl";
import { useConnector } from "@/lib/hooks/useConnector";
import { targetNodeIds } from "@/lib/connector-nodes";
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
  // Hand a resolved draft up to AppShell, which opens the setup wizard
  // (IngestPane) preloaded with it — the "Set up campaign" CTA after check-in.
  onOpenDraft: (draft: DraftCampaignWire, resolution: OriginLastResolution | null) => void;
}

// One durable chat message; the thread renders from a list of these.
type ChatMsg =
  | { id: string; kind: "user-file"; name: string; rows: number | null }
  | { id: string; kind: "ai"; text: string }
  | { id: string; kind: "error"; text: string };

// Transient ingest-pipeline status, separate from the durable thread — it's
// replaced (not appended) as the upload → check-in → ready sequence advances.
type IngestPhase =
  | { stage: "idle" }
  | { stage: "uploading" }
  | { stage: "checkin"; model: string }
  // A dropped file's name matches a dataset already in the collection. Rather
  // than a dead-end 409, the chat offers the safe choices (use existing / save
  // as new) — see docs/specs/m13-dataset-bridge.md § 2.
  | {
      stage: "collision";
      file: File;
      chipId: string;
      existingSlug: string;
      suggestedSlug: string;
    }
  | { stage: "ready"; draft: DraftCampaignWire; resolution: OriginLastResolution | null };

// The Chat surface. The job-bar + pipeline hero + settings card are display-only
// (control plane lands in M12). The live interactive path is dataset ingest:
// drop a tabular file into the input row → upload → origin check-in (fills
// metadata) → an AI recap + "Set up campaign" CTA that opens the setup wizard.
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
  onOpenDraft,
}: Props) {
  const [jobOpen, setJobOpen] = useState(false);
  const [wandOn, setWandOn] = useState(true);
  const [samplesOpen, setSamplesOpen] = useState(false);
  const toggleSamples = () => setSamplesOpen((v) => !v);

  // Dataset-ingest conversation. Starts empty; a dropped file grows it.
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [phase, setPhase] = useState<IngestPhase>({ stage: "idle" });
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const busy = phase.stage === "uploading" || phase.stage === "checkin";

  // Drop a tabular file → upload → run the origin check-in → surface the recap
  // + the "Set up campaign" CTA. Any format the backend rejects (unsupported,
  // hardened-blocked Excel, bad JSON) comes back as a friendly chat error — the
  // UI never crashes on a stray drop. A name collision (409) is NOT an error —
  // it routes to the choice card (use existing / save as new), § 2 of the spec.
  // `slug` re-runs ingest under a chosen name ("save as new"); `chipId` reuses
  // the already-rendered file chip instead of appending a duplicate.
  const ingestAndResolve = async (file: File, slug?: string, chipId?: string) => {
    if (busy) return; // ignore a second drop while one is in flight
    const id = chipId ?? crypto.randomUUID();
    if (!chipId) {
      setMessages((m) => [...m, { id, kind: "user-file", name: file.name, rows: null }]);
    }

    setPhase({ stage: "uploading" });
    let draft: DraftCampaignWire;
    try {
      draft = await postIngestDataset(file, slug);
    } catch (e) {
      if (
        e instanceof IngestApiError &&
        e.status === 409 &&
        e.existingSlug &&
        e.suggestedSlug
      ) {
        setPhase({
          stage: "collision",
          file,
          chipId: id,
          existingSlug: e.existingSlug,
          suggestedSlug: e.suggestedSlug,
        });
        return;
      }
      setPhase({ stage: "idle" });
      setMessages((m) => [
        ...m,
        { id: crypto.randomUUID(), kind: "error", text: IngestApiError.toOperatorMessage(e) },
      ]);
      return;
    }
    // Backfill the chip's row count now that the file is parsed.
    setMessages((m) =>
      m.map((msg) =>
        msg.id === id && msg.kind === "user-file" ? { ...msg, rows: draft.n_samples } : msg,
      ),
    );

    setPhase({ stage: "checkin", model: draft.optimizer_model || "the check-in model" });
    let resolved = draft;
    let resolution: OriginLastResolution | null = null;
    let recap = "";
    try {
      const r = await postResolveOrigin(draft.draft_id);
      resolved = r.draft;
      resolution = r.resolution.last_resolution ?? null;
      recap = resolution?.recap || resolution?.assessment || plainLanguageRecap(resolved);
    } catch (e) {
      // The check-in failed but the draft exists — let the operator proceed to
      // the wizard (which has its own "Set up with AI" retry).
      recap = plainLanguageRecap(resolved);
      setMessages((m) => [
        ...m,
        { id: crypto.randomUUID(), kind: "error", text: IngestApiError.toOperatorMessage(e) },
      ]);
    }
    setMessages((m) => [...m, { id: crypto.randomUUID(), kind: "ai", text: recap }]);
    setPhase({ stage: "ready", draft: resolved, resolution });
  };

  const onDatasetFile = (file: File) => void ingestAndResolve(file);

  // Collision choice: "Replace" — version-and-repoint the existing dataset so
  // its name frees, then re-ingest the dropped file under it. Data-safe: the old
  // data + every prior campaign's results are preserved under `{slug}-vN`, never
  // overwritten (docs/specs/m13-dataset-bridge.md § 2.1). On the migration
  // succeeding, `slug` is free, so the re-ingest runs the normal check-in flow.
  const replaceWithDropped = async (file: File, slug: string, chipId: string) => {
    setPhase({ stage: "uploading" });
    try {
      await postReplaceDataset(slug);
    } catch (e) {
      setPhase({ stage: "idle" });
      setMessages((m) => [
        ...m,
        { id: crypto.randomUUID(), kind: "error", text: IngestApiError.toOperatorMessage(e) },
      ]);
      return;
    }
    await ingestAndResolve(file, slug, chipId);
  };

  // Collision choice: start a new campaign on the dataset already in the
  // collection — no re-ingest. The derived-from-dataset draft is pre-filled, so
  // it opens the wizard directly (no check-in needed).
  const startFromExistingDataset = async (slug: string) => {
    setPhase({ stage: "uploading" });
    try {
      const draft = await postDraftFromDataset(slug);
      setPhase({ stage: "idle" });
      onOpenDraft(draft, null);
    } catch (e) {
      setPhase({ stage: "idle" });
      setMessages((m) => [
        ...m,
        { id: crypto.randomUUID(), kind: "error", text: IngestApiError.toOperatorMessage(e) },
      ]);
    }
  };

  // Shared connector view (one provider-level fetch + health poll).
  const cv = useConnector();
  const { node: selectedNode, setSelectionForNode } = useSelection();
  // Target-node ids are disjoint from the optimizer canvas (membership-gate);
  // the demo/preview hero with no view exposes the synthetic "llm" chip id.
  const showBackendDetail =
    selectedNode != null && targetNodeIds(cv.view).includes(selectedNode);
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
    rateKnown,
    backendTokens,
    loopTokens,
  } = readSpend(dash);
  const budgetChip = budgetUsd != null ? `$${budgetUsd.toFixed(2)}` : "—";
  const deltaPerSpend =
    delta != null && usedUsd != null && usedUsd > 0 ? delta / usedUsd : null;
  const effChip =
    deltaPerSpend != null ? `${(deltaPerSpend * 100).toFixed(2)} pp/$` : "—";

  // ETA to budget — pure client-side derivation. Burn rate = used / cycle_age,
  // ETA = remaining_budget / burn. Renders "—" until spend is wired or when
  // budget is uncapped / already spent.
  const etaChip = (() => {
    if (usedUsd == null || budgetUsd == null || !cycleStartedAt) return "—";
    const startedMs = Date.parse(cycleStartedAt);
    if (!Number.isFinite(startedMs)) return "—";
    // Date.now reads the wallclock once per render to project an ETA; the
    // re-render cadence is driven by dash polling so the value never goes
    // stale visibly.
    const ageSec = (Date.now() - startedMs) / 1000;
    if (ageSec <= 0 || usedUsd <= 0) return "—";
    if (usedUsd >= budgetUsd) return "spent";
    const burn = usedUsd / ageSec; // $/sec
    const remainingSec = (budgetUsd - usedUsd) / burn;
    return fmtDuration(remainingSec);
  })();

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
              <div className="row"><span className="lbl">Budget</span><span className="val">{budgetChip}</span></div>
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
                usedUsd={usedUsd}
              />
            </div>
          </div>
        )}
      </div>
      ) : null}

      <div className="wf-hero">
        <TargetPipelineHero samplesOpen={samplesOpen} onToggle={toggleSamples} cv={cv} />
        {showBackendDetail && (
          <BackendNodeDetail cv={cv} onClose={() => setSelectionForNode(null)} />
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
          <div className="chat-messages" aria-live="polite">
            {/* First-run illustration — left in place until the operator drops a
                file, so a newcomer sees what this surface is for (drop your eval
                set here). Replaced by the live ingest thread on first drop. */}
            {messages.length === 0 && phase.stage === "idle" ? (
              <>
                <div className="chat-msg user">My pipeline above is stuck at 73%. Can&apos;t push past that.</div>
                <div className="chat-msg ai">Share the eval set you&apos;re scoring against?</div>
                <ChatFileChip name="email-tagging.csv" rows={15} />
                <div className="chat-msg ai">Got your pipeline + the project. Flip on Auto-tune (BETA) — I&apos;ll find a better prompt for it. Want me to turn it on?</div>
                <div className="chat-msg ai">Which parameter do you want to explore?</div>
                <div className="chat-msg ai">
                  Few things to tune this right:<br />
                  • <strong>Which evaluators matter most?</strong> Easy picks: speed (time per query), # of websites checked, accuracy, cost per query — pick whatever you care about.<br />
                  • <strong>Preferred LLM</strong>, or should I pick one?<br />
                  • <strong>Pipeline type</strong> — LLM-driven (a model decides each step) or deterministic (fixed rules, no AI in the loop)?<br />
                  • <strong>Time ceiling</strong> — any hard cap on how long one query is allowed to take?
                </div>
              </>
            ) : null}
            {messages.map((msg) =>
              msg.kind === "user-file" ? (
                <ChatFileChip key={msg.id} name={msg.name} rows={msg.rows} />
              ) : msg.kind === "ai" ? (
                <div key={msg.id} className="chat-msg ai">
                  {msg.text}
                </div>
              ) : (
                <div key={msg.id} className="chat-msg ai chat-msg-error" role="alert">
                  {msg.text}
                </div>
              ),
            )}
            {phase.stage === "uploading" ? (
              <p className="checkin-loading" role="status" aria-live="polite">
                Parsing your file…
              </p>
            ) : null}
            {phase.stage === "checkin" ? <CheckinLoadingWindow model={phase.model} /> : null}
            {phase.stage === "collision" ? (
              <div className="chat-msg ai chat-collision" role="group" aria-label="Dataset name already exists">
                <p>
                  You already have a dataset named <strong>{phase.existingSlug}</strong>. What
                  would you like to do?
                </p>
                <div className="chat-collision-actions">
                  <button
                    type="button"
                    className="chat-cta-btn"
                    onClick={() => void startFromExistingDataset(phase.existingSlug)}
                  >
                    Use existing → new campaign
                  </button>
                  <button
                    type="button"
                    className="chat-cta-btn secondary"
                    onClick={() =>
                      void ingestAndResolve(phase.file, phase.suggestedSlug, phase.chipId)
                    }
                  >
                    Save as new ({phase.suggestedSlug})
                  </button>
                  <button
                    type="button"
                    className="chat-cta-btn chat-collision-replace"
                    title={`Archives the current ${phase.existingSlug} (and its campaigns) as a version, then takes its place`}
                    onClick={() =>
                      void replaceWithDropped(phase.file, phase.existingSlug, phase.chipId)
                    }
                  >
                    Replace — keep old data as a version
                  </button>
                  <button
                    type="button"
                    className="chat-collision-cancel"
                    onClick={() => setPhase({ stage: "idle" })}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : null}
            {phase.stage === "ready" ? (
              <div className="chat-msg ai chat-cta">
                <button
                  type="button"
                  className="chat-cta-btn"
                  onClick={() => onOpenDraft(phase.draft, phase.resolution)}
                >
                  Set up campaign
                </button>
              </div>
            ) : null}
          </div>
          <div
            className={cx("chat-input-row", dragging && "is-dragover")}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              const f = e.dataTransfer.files?.[0];
              if (f) void onDatasetFile(f);
            }}
          >
            <input
              ref={fileInputRef}
              type="file"
              hidden
              accept=".csv,.tsv,.json,.jsonl,.ndjson,.xlsx,text/csv,application/json"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void onDatasetFile(f);
                e.target.value = "";
              }}
            />
            <button
              className="chat-attach"
              type="button"
              title="Attach a dataset file"
              aria-label="Attach a dataset file"
              disabled={busy}
              onClick={() => fileInputRef.current?.click()}
            >
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14.5 7.5 8 14a3.5 3.5 0 0 1-4.95-4.95L9.5 2.6a2.4 2.4 0 0 1 3.4 3.4L6.4 12.5a1.3 1.3 0 0 1-1.83-1.83L11 4.2" />
              </svg>
            </button>
            <textarea className="chat-input" placeholder="Drop a CSV, TSV, JSON or Excel file…" rows={1} disabled aria-label="Chat input" />
            <button className="chat-send" type="button" disabled>Send</button>
          </div>
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
