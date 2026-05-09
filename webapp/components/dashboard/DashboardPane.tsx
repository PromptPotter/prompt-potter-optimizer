"use client";
import { useCallback, useEffect, useState } from "react";
import { fetchActive, fetchCycleFile, fetchPipeline } from "@/lib/api";
import { useDashboardPoll, type DashboardSnapshot } from "@/lib/poll";
import { applyChartDefaults } from "@/lib/theme";
import { Chart as ChartJS } from "chart.js";
import { Sidebar, type Pane } from "@/components/shell/Sidebar";
import { Topbar, type Tab } from "@/components/shell/Topbar";
import { StatusBar } from "@/components/status/StatusBar";
import { WorkflowCanvas } from "@/components/workflow/WorkflowCanvas";
import { FitnessPanel } from "@/components/whatif/FitnessPanel";
import { PassRateCard } from "@/components/eval/PassRateCard";
import { FreqChart } from "@/components/eval/FreqChart";
import { TrendChart } from "@/components/eval/TrendChart";
import { EvalTable } from "@/components/eval/EvalTable";
import { RawJsonCard } from "@/components/raw/RawJsonCard";
import { HeroSummary } from "./HeroSummary";
import { ChatPane } from "./ChatPane";
import { ProgressCard } from "./ProgressCard";
import { LiveStateCard } from "./LiveStateCard";
import { LiveSamplesCard } from "./LiveSamplesCard";
import { SignalsPanel } from "./SignalsPanel";
import { StuckDiagnosis } from "./StuckDiagnosis";
import { FilesPane } from "@/components/tree/FilesPane";

interface PipelineDoc {
  view?: { nodes: { id: string; label: string; kind?: string }[]; edges: { from: string; to: string }[] };
  nodes?: Record<string, { type?: string; config?: Record<string, unknown>; model?: string }>;
}

interface RoundData {
  round?: number;
  scoreboard?: { candidate_id?: string; label?: string; composite_fitness?: number; accuracy?: number; is_winner?: boolean }[];
  baseline_accuracy?: number;
  results?: { id?: string | number; query?: string; predicted?: string; ground_truth?: string; score?: number; error?: unknown }[];
  composite_fitness?: number;
  accuracy?: number;
}

export function DashboardPane() {
  // Default to New Job (chat shell) — matches vanilla's first-load tab.active.
  const [tab, setTab] = useState<Tab>("newjob");
  const [pane, setPane] = useState<Pane>("dashboard");
  const [cycleId, setCycleId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [datasetTitle, setDatasetTitle] = useState<string | null>(null);
  const [cycleStartedAt, setCycleStartedAt] = useState<string | null>(null);
  const [pipeline, setPipeline] = useState<PipelineDoc | null>(null);
  const [latestRound, setLatestRound] = useState<RoundData | null>(null);
  const [lastRoundFetched, setLastRoundFetched] = useState<number>(-1);
  const [themeKey, setThemeKey] = useState<string>("init");
  const [activeError, setActiveError] = useState<string | null>(null);

  const dashState = useDashboardPoll(cycleId);
  const dash: DashboardSnapshot | null = dashState.dash;

  // One-shot active session lookup
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const a = await fetchActive();
        if (cancelled) return;
        setCycleId(a.cycle_id);
        setSessionId(a.session_id);
      } catch (e) {
        if (cancelled) return;
        setActiveError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
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

  // Round-based panels: fetch the most recent round JSON when dash.round bumps
  useEffect(() => {
    if (!cycleId || !dash) return;
    const round = Number(dash.round ?? 0);
    if (round <= 0 || round - 1 === lastRoundFetched) return;
    const nn = String(round - 1).padStart(4, "0");
    let cancelled = false;
    (async () => {
      try {
        const r = await fetchCycleFile(cycleId, "cycle", `rounds/round_${nn}.json`);
        const data = JSON.parse(r.content) as RoundData;
        if (cancelled) return;
        setLatestRound(data);
        setLastRoundFetched(round - 1);
      } catch {
        /* round may not be on disk yet */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cycleId, dash, dash?.round, lastRoundFetched]);

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
      <Sidebar pane={pane} onSelect={onPaneSelect} />
      <main className="main">
        <Topbar tab={tab} onTabChange={setTab} onThemeChange={onThemeChange} />
        {tab === "newjob" ? (
          <ChatPane cycleId={cycleId} sessionId={sessionId} datasetTitle={datasetTitle} dash={dash} cycleStartedAt={cycleStartedAt} themeKey={themeKey} />
        ) : pane === "dashboard" ? (
          <div className="content" id="content-dashboard">
            <StatusBar
              kind={dashState.status}
              text={activeError && !cycleId ? "No active session" : dashState.statusText}
              hint={activeError && !cycleId
                ? "Initialize a cycle: `python -m promptpotter init --backend-url http://127.0.0.1:8000 --config datasets/<name>/campaign.json`, then `python -m promptpotter optimize` in another terminal."
                : dashState.statusHint}
              termKey={dashState.termKey}
            />
            <div className="page-header">
              <div className="page-header-text">
                <div className="breadcrumb">
                  Cycle » {cycleId || (activeError ? "no active session" : "loading…")}
                </div>
                <h1>{datasetTitle || cycleId || (activeError ? "No active session" : "Loading…")}</h1>
                <div className="meta">
                  session {sessionId || "—"} • updated {dash?.wallclock_serialized_at || "—"}
                </div>
              </div>
              <HeroSummary cycleId={cycleId} dash={dash} />
            </div>
            <ProgressCard dash={dash} />
            <StuckDiagnosis dash={dash} />
            <WorkflowCanvas pipeline={pipeline} dash={dash} />
            <LiveStateCard dash={dash} />
            <SignalsPanel dash={dash} />
            <div className="grid3">
              <PassRateCard round={latestRound} />
              <FreqChart round={latestRound} dash={dash} themeKey={themeKey} />
              <TrendChart cycleId={cycleId} refreshKey={lastRoundFetched} themeKey={themeKey} />
            </div>
            <FitnessPanel dash={dash} themeKey={themeKey} />
            <LiveSamplesCard dash={dash} />
            <EvalTable round={latestRound} />
            <RawJsonCard dash={dash} />
          </div>
        ) : (
          <FilesPane cycleId={cycleId} />
        )}
      </main>
    </div>
  );
}
