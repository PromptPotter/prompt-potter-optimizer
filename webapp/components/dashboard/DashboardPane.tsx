"use client";
import { useCallback, useEffect, useState } from "react";
import {
  fetchActive,
  fetchActiveDatasetName,
  fetchCycleFile,
  fetchDatasetPreview,
  fetchPipeline,
  type DatasetItem,
} from "@/lib/api";
import {
  CycleStreamProvider,
  roundOf,
  useCycleStream,
  type DashboardSnapshot,
} from "@/lib/poll";
import { applyChartDefaults } from "@/lib/theme";
import { Chart as ChartJS } from "chart.js";
import { Sidebar } from "@/components/shell/Sidebar";
import { Topbar, type Tab } from "@/components/shell/Topbar";
import { StatusBar } from "@/components/status/StatusBar";
import { ConsolePane } from "@/components/console/ConsolePane";
import { WorkflowCanvas } from "@/components/workflow/WorkflowCanvas";
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
import { SelectionProvider } from "./SelectionContext";
import { SharedInspector } from "./SharedInspector";
import { FilesPane } from "@/components/tree/FilesPane";
import { LeveragePanel } from "@/components/leverage/LeveragePanel";
import { ComparePane } from "@/components/compare/ComparePane";

interface PipelineDoc {
  view?: { nodes: { id: string; label: string; kind?: string }[]; edges: { from: string; to: string }[] };
  nodes?: Record<string, { type?: string; config?: Record<string, unknown>; model?: string }>;
}

export function DashboardPane() {
  const [cycleId, setCycleId] = useState<string | null>(null);
  return (
    <CycleStreamProvider cycleId={cycleId}>
      <DashboardPaneInner cycleId={cycleId} setCycleId={setCycleId} />
    </CycleStreamProvider>
  );
}

function DashboardPaneInner({
  cycleId,
  setCycleId,
}: {
  cycleId: string | null;
  setCycleId: (id: string | null) => void;
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
  const [datasetName, setDatasetName] = useState<string | null>(null);
  const [datasetItems, setDatasetItems] = useState<DatasetItem[]>([]);
  const [datasetTrainCount, setDatasetTrainCount] = useState(0);
  const [datasetTestCount, setDatasetTestCount] = useState(0);
  const [themeKey, setThemeKey] = useState<string>("init");
  const [activeError, setActiveError] = useState<string | null>(null);
  // Edit mode — off by default; gates Stop run + Fork-from-here. Never
  // persisted across reloads (no URL param, no localStorage) so the
  // operator never finds risky affordances quietly enabled.
  const [editMode, setEditMode] = useState(false);

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

  // Initial cycle selection — precedence: URL `?cycle=…` > /api/v1/active
  // > null. `sessionId` still comes from /active (the workspace pointer is
  // CLI-owned and doesn't move when the operator browses a different cycle).
  useEffect(() => {
    let cancelled = false;
    const urlCycle =
      typeof window !== "undefined"
        ? new URLSearchParams(window.location.search).get("cycle")
        : null;
    (async () => {
      try {
        const a = await fetchActive();
        if (cancelled) return;
        setSessionId(a.session_id);
        setCycleId(urlCycle || a.cycle_id);
      } catch (e) {
        if (cancelled) return;
        if (urlCycle) {
          setCycleId(urlCycle); // honour deep-link even when no active session
        } else {
          setActiveError((e as Error).message);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // `setCycleId` is the stable useState setter forwarded from the outer
    // DashboardPane wrapper; including it as a dep is harmless but eslint
    // can't see through the prop boundary.
  }, [setCycleId]);

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
  // change (prev-prop pattern); no need to plumb that here.
  const handleCycleChange = useCallback(
    (next: string) => {
      setCycleId(next);
    },
    [setCycleId],
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
  useEffect(() => {
    if (!cycleId) return;
    let cancelled = false;
    const ac = new AbortController();
    (async () => {
      try {
        const name = await fetchActiveDatasetName(cycleId, ac.signal);
        if (cancelled || !name) return;
        setDatasetName(name);
        const preview = await fetchDatasetPreview(name, 1000, ac.signal);
        if (cancelled) return;
        setDatasetItems(preview.items);
        setDatasetTrainCount(preview.train_count);
        setDatasetTestCount(preview.test_count);
      } catch {
        // load may race with cycle-mint; retry on next cycle change
      }
    })();
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [cycleId]);

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
    <div className={`shell${tab === "chat" ? " chat-mode" : ""}`}>
      <Sidebar
        cycleId={cycleId}
        onSelectCycle={(id) => {
          handleCycleChange(id);
          setTab("dashboard");
        }}
        onNewCycle={() => setTab("chat")}
      />
      <main className="main">
        <Topbar tab={tab} onTabChange={setTab} onThemeChange={onThemeChange} />
        <StatusBar
          status={dashState.status}
          statusText={activeError && !cycleId ? "No active campaign" : dashState.statusText}
          statusHint={
            activeError && !cycleId
              ? "Start a campaign: `python -m promptpotter optimize --backend-url http://127.0.0.1:8000 --config datasets/<name>/campaign.json` in another terminal."
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
              <WideSpine>
                <div className="dash-row-now">
                  <WorkflowCanvas pipeline={pipeline} dash={dash} />
                  <LiveStateCard dash={dash} />
                </div>
              </WideSpine>
              <WideSpine>
                <section className="dash-samples-wide" aria-label="Live samples">
                  <LiveSamplesCard dash={dash} dashRound={dashRound} status={dashState.status} />
                </section>
              </WideSpine>
            </Lane>
            <Lane id="verdict" title="Verdict" subtitle="Has it improved? By how much?" defaultOpen>
              <WideSpine>
                <div className="dash-row-verdict">
                  <LineageTree dash={dash} />
                  <FitnessPanel dash={dash} dashRound={dashRound} cycleId={cycleId} themeKey={themeKey} />
                </div>
              </WideSpine>
              <WideSpine>
                <div className="dash-charts">
                  <FreqChart dash={dash} themeKey={themeKey} />
                  <TrendChart themeKey={themeKey} />
                </div>
              </WideSpine>
            </Lane>
            <Lane id="why" title="Why" subtitle="Drill into a candidate or node" defaultOpen={false}>
              <WideSpine>
                <SharedInspector cycleId={cycleId} dash={dash} pipeline={pipeline} isLive={isLive} />
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

