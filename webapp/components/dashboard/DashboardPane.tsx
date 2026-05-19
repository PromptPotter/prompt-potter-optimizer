"use client";
import { useCallback, useEffect, useState } from "react";
import {
  fetchActive,
  fetchActiveDatasetName,
  fetchCycleFile,
  fetchDatasetPipeline,
  fetchDatasetPreview,
  fetchMeasurementSeries,
  fetchPipeline,
  type DatasetItem,
  type HardSamplesScope,
  type MeasurementDot,
} from "@/lib/api";
import {
  CycleStreamProvider,
  roundOf,
  useCycleStream,
  type DashboardSnapshot,
  type RoundFileDoc,
} from "@/lib/poll";
import { applyChartDefaults } from "@/lib/theme";
import { Chart as ChartJS } from "chart.js";
import { Sidebar } from "@/components/shell/Sidebar";
import { Topbar, type Tab } from "@/components/shell/Topbar";
import { StatusBar } from "@/components/status/StatusBar";
import { ConsolePane } from "@/components/console/ConsolePane";
import { WorkflowCanvas } from "@/components/workflow/WorkflowCanvas";
import { OptimizerNodeDetail } from "@/components/workflow/OptimizerNodeDetail";
import { FitnessPanel } from "@/components/whatif/FitnessPanel";
import { FreqChart } from "@/components/eval/FreqChart";
import { TrendChart } from "@/components/eval/TrendChart";
import { RawJsonCard } from "@/components/raw/RawJsonCard";
import { TopStrip } from "./TopStrip";
import { ChatPane } from "./ChatPane";
import { NarrowSpine, WideSpine } from "./DashboardLayout";
import { Lane } from "./Lane";
import { LiveStateCard } from "./LiveStateCard";
import { LiveSamplesCard } from "./LiveSamplesCard";
import { LineageTree } from "./LineageTree";
import { CyclePicker } from "./CyclePicker";
import { EditModeToggle } from "./EditModeToggle";
import { SelectionProvider, useSelection } from "./SelectionContext";
import { SharedInspector } from "./SharedInspector";
import { FilesPane } from "@/components/tree/FilesPane";
import { LeveragePanel } from "@/components/leverage/LeveragePanel";
import { ComparePane } from "@/components/compare/ComparePane";

import type { PipelineDoc, PipelineView } from "@/components/workflow/types";

export function DashboardPane() {
  const [cycleId, setCycleId] = useState<string | null>(null);
  // Auto-follow the server's active cycle pointer until the operator picks
  // one manually. URL ?cycle=… deep-link counts as a manual pick (set on
  // mount in the initial-fetch effect below).
  const [autoFollow, setAutoFollow] = useState(true);
  return (
    <CycleStreamProvider cycleId={cycleId}>
      <DashboardPaneInner
        cycleId={cycleId}
        setCycleId={setCycleId}
        autoFollow={autoFollow}
        setAutoFollow={setAutoFollow}
      />
    </CycleStreamProvider>
  );
}

function DashboardPaneInner({
  cycleId,
  setCycleId,
  autoFollow,
  setAutoFollow,
}: {
  cycleId: string | null;
  setCycleId: (id: string | null) => void;
  autoFollow: boolean;
  setAutoFollow: (v: boolean) => void;
}) {
  // Replit-style sub-tabs (Chat / Dashboard / Files) scoped to the
  // currently-selected cycle. The sidebar is persistent across all
  // three. Default = chat: that's where new cycles get conceived and
  // where the conversational interface lives. Flipping the default to
  // "dashboard" on initial mount tripped React #185 — the hero/fitness
  // chain doesn't handle null→non-null cycleId during the first paint;
  // chat dodges that path. Revisit when we untangle the dashboard
  // first-render sequence.
  const [tab, setTab] = useState<Tab>("chat");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [datasetTitle, setDatasetTitle] = useState<string | null>(null);
  const [cycleStartedAt, setCycleStartedAt] = useState<string | null>(null);
  const [pipeline, setPipeline] = useState<PipelineDoc | null>(null);
  // Target connector pipeline view — what the dataset's `pipelines.default`
  // actually wires up. Drives the data-driven chat-pane hero (N=1 keeps the
  // single-LLM chip look; N≥2 renders dot+outside-label chain). Fetched once
  // per datasetName change.
  const [targetPipelineView, setTargetPipelineView] = useState<PipelineView | null>(null);
  // Original-cased connector name (e.g. "TermNorm Local") rendered as a
  // small uppercase tag inside the multi-node chip. Same fetch as the view.
  const [targetConnector, setTargetConnector] = useState<string | null>(null);
  const [datasetName, setDatasetName] = useState<string | null>(null);
  const [datasetItems, setDatasetItems] = useState<DatasetItem[]>([]);
  const [datasetTrainCount, setDatasetTrainCount] = useState(0);
  const [datasetTestCount, setDatasetTestCount] = useState(0);
  // Per-sample measurement series feeding the Meas heat-map column. Sourced
  // from the new /datasets/{name}/measurement-series endpoint, refreshed
  // whenever the scope toggle flips so workspace/campaign actually swap the
  // dot history (not just miss_prob).
  const [archivePerSample, setArchivePerSample] = useState<Map<number, MeasurementDot[]>>(
    () => new Map(),
  );
  // Hard-sample view scope — workspace = cross-cycle archive, campaign =
  // this cycle only. Default workspace (matches the original always-archive
  // behaviour); persisted in localStorage so the operator's choice survives
  // reloads. Re-fetches the dataset preview when toggled.
  const [hardSamplesScope, setHardSamplesScope] = useState<HardSamplesScope>("workspace");
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem("promptpotter.hardSamples.scope");
      if (raw === "campaign" || raw === "workspace") setHardSamplesScope(raw);
    } catch {
      /* ignore */
    }
  }, []);
  const updateHardSamplesScope = useCallback((next: HardSamplesScope) => {
    setHardSamplesScope(next);
    try {
      window.localStorage.setItem("promptpotter.hardSamples.scope", next);
    } catch {
      /* ignore */
    }
  }, []);
  const [themeKey, setThemeKey] = useState<string>("init");
  const [activeError, setActiveError] = useState<string | null>(null);
  // Edit mode — off by default; gates Stop run + Fork-from-here. Never
  // persisted across reloads (no URL param, no localStorage) so the
  // operator never finds risky affordances quietly enabled.
  const [editMode, setEditMode] = useState(false);
  // Sidebar collapse — user-driven, persistent across reloads. Default
  // expanded; once the user collapses it, that sticks until they toggle
  // again. Tab switches never touch this state — that's the whole point
  // of the manual control.
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  useEffect(() => {
    try {
      setSidebarCollapsed(window.localStorage.getItem("promptpotter.sidebar.collapsed") === "1");
    } catch {
      /* private mode etc. */
    }
  }, []);
  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem("promptpotter.sidebar.collapsed", next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  const dashState = useCycleStream();
  const dash: DashboardSnapshot | null = dashState.dash;
  // Canonical round number — derived once, threaded down to children that
  // need it (LiveSamplesCard, FitnessPanel, HardSamples*). The
  // CycleStreamProvider above owns the round-files cache: every consumer
  // that previously called `useRoundHistory(cycleId, refreshKey)` now reads
  // `useCycleStream().rounds`, so the dashboard makes one set of round-
  // file network calls per round change instead of one per panel.
  const dashRound = roundOf(dash);
  // Liveness — used by mid-run guards (three-way fork modal, Live badge,
  // Stop button visibility). The stream already classifies the poll's
  // success/age into "live"/"stale"/"offline" — reuse that signal instead
  // of inventing a second one.
  const isLive = dashState.status === "live";

  // Initial cycle selection + auto-follow poll. Precedence on mount:
  //   URL `?cycle=…` > /api/v1/active > null
  // A URL deep-link pins the view (autoFollow flipped off on first tick);
  // otherwise we track the server's active pointer every 3 s so a CLI-side
  // mint / fork moves the main panel in lock-step with the sidebar's orange
  // dot. The sidebar (CyclePicker) already follows active_cycle_id; this
  // closes the last source-of-truth gap between it and the dashboard.
  //
  // Effect deps include `cycleId` + `autoFollow` so each manual pick / auto-
  // follow swap rebuilds the interval with fresh closures. That's one extra
  // fetch per rare cycle change — cheap.
  //
  // `sessionId` still comes from /active (the workspace pointer is
  // CLI-owned and doesn't move when the operator browses a different cycle).
  useEffect(() => {
    let cancelled = false;
    const urlCycle =
      typeof window !== "undefined"
        ? new URLSearchParams(window.location.search).get("cycle")
        : null;
    const tick = async () => {
      try {
        const a = await fetchActive();
        if (cancelled) return;
        setSessionId(a.session_id);
        setActiveError(null);
        if (cycleId === null) {
          // Mount path: seed cycleId from /active (or the URL pin). A URL
          // deep-link counts as a manual pick — drop out of auto-follow.
          if (urlCycle) setAutoFollow(false);
          setCycleId(urlCycle || a.cycle_id);
        } else if (autoFollow && a.cycle_id && a.cycle_id !== cycleId) {
          // Server-side active pointer moved (CLI mint / fork) while we
          // were still auto-following — swing the view to follow it.
          setCycleId(a.cycle_id);
        }
      } catch (e) {
        if (cancelled) return;
        if (cycleId === null && urlCycle) {
          setAutoFollow(false);
          setCycleId(urlCycle); // honour deep-link even when no active session
        } else if (cycleId === null) {
          setActiveError((e as Error).message);
        }
      }
    };
    void tick();
    const handle = window.setInterval(() => void tick(), 3000);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [setCycleId, setAutoFollow, cycleId, autoFollow]);

  // Keep ?cycle=… in the URL so reloads stick to the currently-viewed cycle.
  // replaceState (not pushState) — switching cycles isn't a navigation event,
  // we don't want a back-button entry per pick.
  useEffect(() => {
    if (typeof window === "undefined" || !cycleId) return;
    const params = new URLSearchParams(window.location.search);
    if (params.get("cycle") === cycleId) return;
    params.set("cycle", cycleId);
    window.history.replaceState(null, "", `?${params.toString()}`);
  }, [cycleId]);

  // Picker handoff. SelectionProvider auto-clears its slot on cycleId
  // change (prev-prop pattern); no need to plumb that here. Picking a
  // cycle manually pins the view — `autoFollow` flips off so subsequent
  // CLI-side mints don't yank the operator off whatever they're inspecting.
  const handleCycleChange = useCallback(
    (next: string) => {
      setAutoFollow(false);
      setCycleId(next);
    },
    [setCycleId, setAutoFollow],
  );

  // One-shot pipeline (topology) lookup
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const p = await fetchPipeline();
        if (!cancelled) setPipeline(p as PipelineDoc);
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Target connector pipeline view per active dataset. Same one-shot pattern
  // as the optimizer pipeline — the dataset's `pipelines.default` is bound
  // at cycle-identity hash time and doesn't mutate mid-loop.
  useEffect(() => {
    if (!datasetName) {
      setTargetPipelineView(null);
      setTargetConnector(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const resp = (await fetchDatasetPipeline(datasetName)) as {
          view?: PipelineView | null;
          connector?: string | null;
        };
        if (!cancelled) {
          setTargetPipelineView(resp?.view ?? null);
          setTargetConnector(resp?.connector ?? null);
        }
      } catch {
        if (!cancelled) {
          setTargetPipelineView(null);
          setTargetConnector(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [datasetName]);

  // Cycle title (dataset name) from index.json
  useEffect(() => {
    if (!cycleId) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await fetchCycleFile(cycleId, "cycle", "index.json");
        const idx = JSON.parse(r.content);
        if (!cancelled) {
          setDatasetTitle(idx.dataset_name || idx.cycle_id || cycleId);
          setCycleStartedAt(typeof idx.created_at === "string" ? idx.created_at : null);
        }
      } catch {
        if (!cancelled) setDatasetTitle(cycleId);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cycleId]);

  // Dataset preview — one fetch per cycle, threaded down through ChatPane to
  // HardSamplesHeatmap → HardSamplesTable. Owning it here means a tab swap
  // (New Job ↔ View Results) doesn't re-fetch, and the heat-map and table
  // both read the same data instead of each running their own fetch chain.
  //
  // Bounded retry on transient failure: the active-cycle poll races
  // `python -m promptpotter new <ds>` — the dashboard sees the new cycleId
  // before `index.json` lands, fetchActiveDatasetName throws, and without a
  // retry the heat-map icon stays hidden forever (HardSamplesHeatmap returns
  // null on empty datasetItems). Retry on a 2 s timer until success or the
  // cycle changes (cleanup clears the timer).
  useEffect(() => {
    if (!cycleId) return;
    let cancelled = false;
    const ac = new AbortController();
    let retryHandle: number | null = null;
    const attempt = async () => {
      try {
        const name = await fetchActiveDatasetName(cycleId, ac.signal);
        if (cancelled || !name) {
          if (!cancelled && !name) scheduleRetry();
          return;
        }
        setDatasetName(name);
        const [preview, seriesRes] = await Promise.all([
          fetchDatasetPreview(name, 1000, ac.signal, hardSamplesScope, cycleId),
          fetchMeasurementSeries(name, 1000, ac.signal, hardSamplesScope, cycleId).catch(
            // Campaign scope before round 1 returns 404 (no
            // hard_samples_campaign.json yet) — fall back to an empty map
            // rather than dropping the whole preview alongside it.
            () => null,
          ),
        ]);
        if (cancelled) return;
        setDatasetItems(preview.items);
        setDatasetTrainCount(preview.train_count);
        setDatasetTestCount(preview.test_count);
        const m = new Map<number, MeasurementDot[]>();
        for (const s of seriesRes?.items ?? []) m.set(s.sample_id, s.measurements);
        setArchivePerSample(m);
      } catch {
        // Transient failure (mint race, 404 before index.json lands). Retry
        // until the cycle changes; cleanup cancels the pending timer.
        if (!cancelled) scheduleRetry();
      }
    };
    const scheduleRetry = () => {
      if (cancelled || retryHandle != null) return;
      retryHandle = window.setTimeout(() => {
        retryHandle = null;
        void attempt();
      }, 2000);
    };
    void attempt();
    return () => {
      cancelled = true;
      ac.abort();
      if (retryHandle != null) window.clearTimeout(retryHandle);
    };
  }, [cycleId, hardSamplesScope]);

  // Cycle change ⇒ clear per-cycle derived state. Prev-prop pattern (React's
  // documented "adjusting state when a prop changes" recipe) avoids the
  // cascading render that `setState` in `useEffect` would cost. The
  // CycleStreamProvider owns the round-file + dashboard reset; this only
  // handles the dataset preview owned by this component.
  const [prevCycleId, setPrevCycleId] = useState<string | null>(cycleId);
  if (cycleId !== prevCycleId) {
    setPrevCycleId(cycleId);
    setDatasetName(null);
    setDatasetItems([]);
    setDatasetTrainCount(0);
    setDatasetTestCount(0);
    setArchivePerSample(new Map());
  }

  // Re-applies chart defaults on theme swap; the themeKey bump forces all
  // chart components to remount so they pick up the new --color-* values.
  const onThemeChange = useCallback(() => {
    applyChartDefaults(ChartJS);
    setThemeKey(`t-${Date.now()}`);
  }, []);

  // Apply chart defaults once on mount.
  useEffect(() => {
    applyChartDefaults(ChartJS);
  }, []);

  return (
    <SelectionProvider cycleId={cycleId}>
    <div className={`shell${tab === "chat" ? " chat-mode sidebar-hidden" : sidebarCollapsed ? " sidebar-collapsed" : ""}`}>
      <Sidebar
        cycleId={cycleId}
        onSelectCycle={(id) => {
          handleCycleChange(id);
          setTab("dashboard");
        }}
        onNewCycle={() => setTab("chat")}
        collapsed={sidebarCollapsed}
        onToggleCollapse={toggleSidebar}
      />
      <main className="main">
        <Topbar tab={tab} onTabChange={setTab} onThemeChange={onThemeChange} />
        <StatusBar
          status={dashState.status}
          statusText={activeError && !cycleId ? "No active campaign" : dashState.statusText}
          statusHint={
            activeError && !cycleId
              ? "Start a campaign: `python -m promptpotter new <dataset>` in another terminal."
              : dashState.statusHint
          }
          termKey={dashState.termKey}
          cycleId={cycleId}
          dash={dash}
          isLive={isLive}
          onOpenFiles={() => setTab("files")}
        />
        {tab === "chat" ? (
          <ChatPane
            cycleId={cycleId}
            sessionId={sessionId}
            datasetTitle={datasetTitle}
            dash={dash}
            dashRound={dashRound}
            cycleStartedAt={cycleStartedAt}
            themeKey={themeKey}
            datasetName={datasetName}
            datasetItems={datasetItems}
            datasetTrainCount={datasetTrainCount}
            datasetTestCount={datasetTestCount}
            archivePerSample={archivePerSample}
            hardSamplesScope={hardSamplesScope}
            onHardSamplesScopeChange={updateHardSamplesScope}
            targetPipelineView={targetPipelineView}
            targetConnector={targetConnector}
          />
        ) : tab === "dashboard" ? (
          <div className="content" id="content-dashboard">
            <NarrowSpine>
              <header className="dash-hero">
                <div className="page-header">
                  <div className="breadcrumb">
                    Campaign »{" "}
                    <CyclePicker cycleId={cycleId} onChange={handleCycleChange} />
                    {isLive && (
                      <span className="live-badge" title="Campaign is actively running — dashboard updated in the last 60s">
                        ● Live
                      </span>
                    )}
                    <span className="cycle-toolbar">
                      <EditModeToggle on={editMode} onToggle={setEditMode} />
                    </span>
                  </div>
                </div>
              </header>
            </NarrowSpine>
            <Lane id="now" title="Now" subtitle="What's running right now" defaultOpen>
              <NarrowSpine>
                <TopStrip dash={dash} dashRound={dashRound} />
              </NarrowSpine>
              <NarrowSpine>
                <NowRow
                  dash={dash}
                  dashRound={dashRound}
                  pipeline={pipeline}
                  cycleId={cycleId}
                  themeKey={themeKey}
                  rounds={dashState.rounds}
                  onSelectCycle={handleCycleChange}
                />
              </NarrowSpine>
              <NarrowSpine>
                <LiveStateCard dash={dash} />
              </NarrowSpine>
              <WideSpine>
                <section className="dash-samples-wide" aria-label="Live samples">
                  <LiveSamplesCard dash={dash} dashRound={dashRound} status={dashState.status} />
                </section>
              </WideSpine>
            </Lane>
            <Lane id="verdict" title="Verdict" subtitle="Has it improved? By how much?" defaultOpen>
              <NarrowSpine>
                <div className="dash-charts">
                  <FreqChart dash={dash} themeKey={themeKey} />
                  <TrendChart themeKey={themeKey} />
                </div>
              </NarrowSpine>
            </Lane>
            <Lane id="why" title="Why" subtitle="Drill into a candidate" defaultOpen={false}>
              <WideSpine>
                <SharedInspector cycleId={cycleId} isLive={isLive} />
              </WideSpine>
              <NarrowSpine>
                <RawJsonCard dash={dash} />
              </NarrowSpine>
            </Lane>
          </div>
        ) : tab === "files" ? (
          <FilesPane cycleId={cycleId} />
        ) : tab === "compare" ? (
          <ComparePane themeKey={themeKey} />
        ) : (
          <LeveragePanel />
        )}
        <ConsolePane cycleId={cycleId} />
      </main>
    </div>
    </SelectionProvider>
  );
}

// The Now lane's main row + drill-down. Always renders the 3-col
// Lineage/Fitness/Optimizer strip so the operator can keep clicking
// between optimizer nodes; when a node is selected, appends
// OptimizerNodeDetail beneath the row as a slave content panel rather
// than swapping the navigator out. Lives inside SelectionProvider so it
// can call useSelection.
function NowRow({
  dash,
  dashRound,
  pipeline,
  cycleId,
  themeKey,
  rounds,
  onSelectCycle,
}: {
  dash: DashboardSnapshot | null;
  dashRound: number | null;
  pipeline: PipelineDoc | null;
  cycleId: string | null;
  themeKey: string;
  rounds: RoundFileDoc[];
  onSelectCycle: (id: string) => void;
}) {
  const { node, setNode } = useSelection();
  return (
    <>
      {/* Two-row grid via grid-areas in CSS:
            row 1: FitnessPanel spans full width (over lineage + workflow)
            row 2: LineageTree (2fr, content-height) + WorkflowCanvas (1fr)
          Render order matches source order so the grid-area assignment
          is the only thing that decides placement. */}
      <div className="dash-row-verdict">
        <FitnessPanel dash={dash} dashRound={dashRound} cycleId={cycleId} themeKey={themeKey} />
        <LineageTree dash={dash} cycleId={cycleId} onSelectCycle={onSelectCycle} />
        <WorkflowCanvas pipeline={pipeline} dash={dash} />
      </div>
      {node && (
        <OptimizerNodeDetail
          id={node}
          pipeline={pipeline}
          dash={dash}
          rounds={rounds}
          onClose={() => setNode(null)}
        />
      )}
    </>
  );
}

