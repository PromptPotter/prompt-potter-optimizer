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
import { roundOf, useDashboardPoll, type DashboardSnapshot, type StatusKind } from "@/lib/poll";
import { applyChartDefaults } from "@/lib/theme";
import { Chart as ChartJS } from "chart.js";
import { Sidebar, type Pane } from "@/components/shell/Sidebar";
import { Topbar, type Tab } from "@/components/shell/Topbar";
import { WorkflowCanvas } from "@/components/workflow/WorkflowCanvas";
import { FitnessPanel } from "@/components/whatif/FitnessPanel";
import { FreqChart } from "@/components/eval/FreqChart";
import { TrendChart } from "@/components/eval/TrendChart";
import { RawJsonCard } from "@/components/raw/RawJsonCard";
import { TERMS } from "@/lib/terms";
import { HeroSummary } from "./HeroSummary";
import { ChatPane } from "./ChatPane";
import { ProgressCard } from "./ProgressCard";
import { LiveStateCard } from "./LiveStateCard";
import { LiveSamplesCard } from "./LiveSamplesCard";
import { LineageTree, type SelectedCandidate } from "./LineageTree";
import { CyclePicker } from "./CyclePicker";
import { ScoringInspector } from "./ScoringInspector";
import { EditModeToggle } from "./EditModeToggle";
import { StopButton } from "./StopButton";
import { FilesPane } from "@/components/tree/FilesPane";

interface PipelineDoc {
  view?: { nodes: { id: string; label: string; kind?: string }[]; edges: { from: string; to: string }[] };
  nodes?: Record<string, { type?: string; config?: Record<string, unknown>; model?: string }>;
}

export function DashboardPane() {
  // Default to New Job — matches the prior first-load tab. The default
  // landing surface is being moved to View Results elsewhere; flipping
  // this state here without untangling the chat-shell's mount sequence
  // tripped React #185. Leaving the default as-is until that's wired.
  const [tab, setTab] = useState<Tab>("newjob");
  const [pane, setPane] = useState<Pane>("dashboard");
  const [cycleId, setCycleId] = useState<string | null>(null);
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
  // Lineage node selection — null = no node selected, inspector hidden.
  const [selected, setSelected] = useState<SelectedCandidate | null>(null);
  // Edit mode — off by default; gates Stop run + Fork-from-here. Never
  // persisted across reloads (no URL param, no localStorage) so the
  // operator never finds risky affordances quietly enabled.
  const [editMode, setEditMode] = useState(false);

  const dashState = useDashboardPoll(cycleId);
  const dash: DashboardSnapshot | null = dashState.dash;
  // Canonical round number — derived once, threaded down to children that
  // need it (ProgressCard, LiveSamplesCard, FitnessPanel, HardSamples*).
  // Doubles as the refreshKey for round-file consumers: bumping it triggers
  // `useRoundHistory` to refetch the round listing, which is the single
  // source for every component that reads round_NNNN.json on disk.
  const dashRound = roundOf(dash);
  const refreshKey = dashRound ?? -1;

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
  }, []);

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

  // Picker handoff: clear any lineage selection when the operator picks a
  // different cycle — a stale candidate id points at the wrong cycle's tree.
  // Done inline rather than in a `useEffect([cycleId])` so eslint's
  // react-hooks/set-state-in-effect doesn't flag it.
  const handleCycleChange = useCallback((next: string) => {
    setCycleId(next);
    setSelected(null);
  }, []);

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
  // cascading render that `setState` in `useEffect` would cost. Round-file
  // state owns its own reset in `useRoundHistory`; this only handles the
  // dataset preview owned by this component.
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

  // Sidebar tab clicks should also flip the top-tab to View Results so the
  // operator never lands on Files while the New Job tab is "active".
  const onPaneSelect = useCallback((p: Pane) => {
    setPane(p);
    setTab("results");
  }, []);

  return (
    <div className={`shell${tab === "newjob" ? " chat-mode" : ""}`}>
      <Sidebar
        pane={pane}
        onSelect={onPaneSelect}
        cycleId={cycleId}
        onSelectCycle={(id) => {
          handleCycleChange(id);
          setTab("results");
          setPane("dashboard");
        }}
      />
      <main className="main">
        <Topbar tab={tab} onTabChange={setTab} onThemeChange={onThemeChange} />
        {tab === "newjob" ? (
          <ChatPane
            cycleId={cycleId}
            sessionId={sessionId}
            datasetTitle={datasetTitle}
            dash={dash}
            dashRound={dashRound}
            cycleStartedAt={cycleStartedAt}
            themeKey={themeKey}
            refreshKey={refreshKey}
            datasetName={datasetName}
            datasetItems={datasetItems}
            datasetTrainCount={datasetTrainCount}
            datasetTestCount={datasetTestCount}
          />
        ) : pane === "dashboard" ? (
          <div className="content" id="content-dashboard">
            <DashStatusStrip
              status={dashState.status}
              statusText={activeError && !cycleId ? "No active session" : dashState.statusText}
              statusHint={activeError && !cycleId
                ? "Initialize a cycle: `python -m promptpotter init --backend-url http://127.0.0.1:8000 --config datasets/<name>/campaign.json`, then `python -m promptpotter optimize` in another terminal."
                : dashState.statusHint}
              termKey={dashState.termKey}
              cycleId={cycleId}
              dash={dash}
              onOpenFiles={() => onPaneSelect("files")}
            />
            <header className="dash-hero">
              <div className="page-header">
                <div className="page-header-text">
                  <div className="breadcrumb">
                    Cycle »{" "}
                    <CyclePicker cycleId={cycleId} onChange={handleCycleChange} />
                    <span className="cycle-toolbar">
                      <EditModeToggle on={editMode} onToggle={setEditMode} />
                      {editMode && cycleId && <StopButton cycleId={cycleId} />}
                    </span>
                  </div>
                  <h1>{datasetTitle || cycleId || (activeError ? "No active session" : "Loading…")}</h1>
                  <div className="meta">
                    session {sessionId || "—"} • updated {dash?.wallclock_serialized_at || "—"}
                  </div>
                </div>
                <HeroSummary cycleId={cycleId} dash={dash} refreshKey={refreshKey} />
              </div>
              <ProgressCard dash={dash} dashRound={dashRound} />
            </header>
            <section className="dash-top" aria-label="Pipeline">
              <WorkflowCanvas pipeline={pipeline} dash={dash} />
            </section>
            <div className="dash-grid">
              <FitnessPanel dash={dash} dashRound={dashRound} cycleId={cycleId} refreshKey={refreshKey} themeKey={themeKey} />
              <LineageTree
                cycleId={cycleId}
                refreshKey={refreshKey}
                dash={dash}
                selected={selected}
                onSelect={setSelected}
              />
            </div>
            {selected && (
              <ScoringInspector
                cycleId={cycleId}
                refreshKey={refreshKey}
                selected={selected}
                editMode={editMode}
                onClose={() => setSelected(null)}
              />
            )}
            {/* Clear selection when the operator switches cycles — stale
               candidate ids would point at the wrong tree otherwise. */}
            <section className="dash-samples-wide" aria-label="Live samples">
              <LiveSamplesCard dash={dash} dashRound={dashRound} status={dashState.status} />
            </section>
            <div className="dash-charts">
              <FreqChart cycleId={cycleId} refreshKey={refreshKey} dash={dash} themeKey={themeKey} />
              <TrendChart cycleId={cycleId} refreshKey={refreshKey} themeKey={themeKey} />
            </div>
            <details className="dash-diag">
              <summary>Diagnostics — live state & raw payload</summary>
              <div className="dash-diag-body">
                <LiveStateCard dash={dash} />
                <RawJsonCard dash={dash} />
              </div>
            </details>
          </div>
        ) : (
          <FilesPane cycleId={cycleId} />
        )}
      </main>
    </div>
  );
}

interface DashStatusStripProps {
  status: StatusKind;
  statusText: string;
  statusHint?: string;
  termKey?: string;
  cycleId: string | null;
  dash: DashboardSnapshot | null;
  onOpenFiles: () => void;
}

function fmtPct(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : "—";
}

function shortCycleId(id: string | null): string {
  if (!id) return "—";
  return id.length > 22 ? `${id.slice(0, 14)}…${id.slice(-4)}` : id;
}

function ageText(iso: string | undefined | null): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

function DashStatusStrip({ status, statusText, statusHint, termKey, cycleId, dash, onOpenFiles }: DashStatusStripProps) {
  const tip = termKey ? TERMS[termKey] : "";
  const round = roundOf(dash);
  const patience = (dash as { patience?: string } | null)?.patience;
  const best = typeof dash?.best === "number" ? dash.best : null;
  const origin = typeof dash?.origin_accuracy === "number" ? dash.origin_accuracy : null;
  const delta = best != null && origin != null ? best - origin : null;
  const deltaSign = delta == null ? "" : delta > 0 ? "+" : "";
  const deltaCls = delta == null ? "" : delta > 0 ? "up" : delta < 0 ? "down" : "flat";
  return (
    <div className={`dash-strip status-bar ${status}`} role="status" aria-live="polite" aria-atomic="true" title={tip || undefined}>
      <span className="status-dot" aria-hidden="true" />
      <span className="dash-strip-text">
        <strong>{statusText}</strong>
        {statusHint ? <span className="dash-strip-hint">{statusHint}</span> : null}
      </span>
      <span className="dash-strip-sep" aria-hidden="true">·</span>
      <span className="dash-strip-cell" title={cycleId ?? ""}>
        <span className="dash-strip-label">cycle</span>
        <code>{shortCycleId(cycleId)}</code>
      </span>
      <span className="dash-strip-cell">
        <span className="dash-strip-label">round</span>
        <strong>{round != null ? `R${round}` : "—"}</strong>
        {patience ? <span className="dash-strip-sub">· patience {patience}</span> : null}
      </span>
      <span className="dash-strip-cell">
        <span className="dash-strip-label">best</span>
        <strong>{fmtPct(best)}</strong>
        {delta != null && origin != null ? (
          <span className={`dash-strip-delta ${deltaCls}`}>{deltaSign}{(delta * 100).toFixed(1)}% vs origin</span>
        ) : null}
      </span>
      <span className="dash-strip-cell">
        <span className="dash-strip-label">updated</span>
        <strong>{ageText(dash?.wallclock_serialized_at)}</strong>
      </span>
      <button type="button" className="dash-strip-jump" onClick={onOpenFiles} aria-label="Open files pane">
        Files →
      </button>
    </div>
  );
}
