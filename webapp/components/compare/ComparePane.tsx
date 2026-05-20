"use client";
// Compare two campaigns side-by-side — trajectory + summary cards. Static
// reads via the existing /backends/{bid}/campaigns/{cid} endpoint; no
// live polling and no new API. Same-dataset constraint keeps it apples-
// to-apples. M13 Slice B; see docs/specs/m13-chat-first-user-web.md.

import { useEffect, useMemo, useState } from "react";
import { Line } from "react-chartjs-2";
import type { ChartData, ChartOptions } from "chart.js";
import {
  fetchCampaignDetail,
  type CampaignDetail,
  type CycleListEntry,
} from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

interface Props {
  themeKey: string;
}

interface LoadedCampaign {
  cycle: CycleListEntry;
  detail: CampaignDetail;
}

function fmtPct(v: number | null | undefined): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function fmtDelta(a: number | null, b: number | null): string {
  if (a == null || b == null) return "—";
  const d = (b - a) * 100;
  if (Math.abs(d) < 0.05) return "0.0pp";
  return `${d > 0 ? "+" : ""}${d.toFixed(1)}pp`;
}

function trajectory(detail: CampaignDetail): { x: number; y: number }[] {
  const out: { x: number; y: number }[] = [];
  if (typeof detail.origin_accuracy === "number") {
    out.push({ x: 0, y: detail.origin_accuracy });
  }
  for (const r of detail.rounds) {
    if (typeof r.accuracy === "number") {
      out.push({ x: r.round, y: r.accuracy });
    }
  }
  return out;
}

export function ComparePane({ themeKey }: Props) {
  // The campaign list comes from the shared workspace context — same list
  // the sidebar and breadcrumb read, kept current by one poll.
  const { cycles, cyclesError } = useWorkspace();
  const [leftId, setLeftId] = useState<string | null>(null);
  const [rightId, setRightId] = useState<string | null>(null);
  const [left, setLeft] = useState<LoadedCampaign | null>(null);
  const [right, setRight] = useState<LoadedCampaign | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Filter eligible right-side cycles to the same dataset as the left pick
  // (apples-to-apples). When no left pick yet, show all.
  const leftEntry = cycles?.find((c) => c.cycle_id === leftId) ?? null;
  const rightEligible = useMemo(() => {
    if (!cycles) return [];
    if (!leftEntry) return cycles;
    return cycles.filter(
      (c) => c.dataset_name === leftEntry.dataset_name && c.cycle_id !== leftEntry.cycle_id,
    );
  }, [cycles, leftEntry]);

  // Load left/right details on selection.
  useEffect(() => {
    if (!leftId || !cycles) {
      setLeft(null);
      return;
    }
    const c = cycles.find((x) => x.cycle_id === leftId);
    if (!c) return;
    let cancelled = false;
    (async () => {
      try {
        const d = await fetchCampaignDetail(c.cycle_id);
        if (!cancelled) setLeft({ cycle: c, detail: d });
      } catch (e) {
        if (!cancelled) setErr((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [leftId, cycles]);

  useEffect(() => {
    if (!rightId || !cycles) {
      setRight(null);
      return;
    }
    const c = cycles.find((x) => x.cycle_id === rightId);
    if (!c) return;
    let cancelled = false;
    (async () => {
      try {
        const d = await fetchCampaignDetail(c.cycle_id);
        if (!cancelled) setRight({ cycle: c, detail: d });
      } catch (e) {
        if (!cancelled) setErr((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [rightId, cycles]);

  // Clear right-side pick when left-side dataset changes (would otherwise leave
  // an apples-to-oranges comparison showing).
  useEffect(() => {
    if (rightId && leftEntry) {
      const r = cycles?.find((c) => c.cycle_id === rightId);
      if (r && r.dataset_name !== leftEntry.dataset_name) {
        setRightId(null);
      }
    }
  }, [leftEntry, rightId, cycles]);

  return (
    <div className="compare-pane">
      <header className="compare-header">
        <h2>Compare campaigns</h2>
        <p className="compare-subtitle">
          Pick two campaigns on the same project to see how their trajectories
          differ. Static read of <code>index.json</code> on disk — no live
          polling.
        </p>
      </header>

      <div className="compare-pickers">
        <ComparePicker
          label="A"
          cycles={cycles}
          selected={leftId}
          onChange={setLeftId}
          disabled={false}
        />
        <span className="compare-vs">vs</span>
        <ComparePicker
          label="B"
          cycles={rightEligible}
          selected={rightId}
          onChange={setRightId}
          disabled={!leftId}
        />
      </div>

      {(err || cyclesError) && (
        <div className="compare-error">Load failed: {err || cyclesError}</div>
      )}

      {(!left || !right) && !err && !cyclesError && (
        <div className="compare-empty">
          {!left
            ? "Pick a campaign for slot A."
            : "Pick a campaign for slot B (same project as A)."}
        </div>
      )}

      {left && right && (
        <>
          <div className="compare-summary">
            <SummaryCard side="A" loaded={left} />
            <SummaryCard side="B" loaded={right} />
          </div>
          <div className="compare-delta-row">
            <DeltaCard
              label="Best vs origin"
              left={left.detail.best_accuracy}
              right={right.detail.best_accuracy}
              format={fmtPct}
            />
            <DeltaCard
              label="Origin"
              left={left.detail.origin_accuracy}
              right={right.detail.origin_accuracy}
              format={fmtPct}
            />
            <DeltaCard
              label="Rounds"
              left={left.detail.n_rounds}
              right={right.detail.n_rounds}
              format={(v) => (v == null ? "—" : String(v))}
            />
          </div>
          <TrajectoryChart left={left} right={right} themeKey={themeKey} />
        </>
      )}
    </div>
  );
}

interface PickerProps {
  label: string;
  cycles: CycleListEntry[] | null;
  selected: string | null;
  onChange: (id: string | null) => void;
  disabled: boolean;
}

function ComparePicker({ label, cycles, selected, onChange, disabled }: PickerProps) {
  // Group by dataset; the operator picks within a project (M13: project ≈
  // dataset_name). Same shape as the dashboard's CyclePicker but
  // standalone (no breadcrumb integration, no refresh button).
  const groups = useMemo(() => {
    const m = new Map<string, CycleListEntry[]>();
    for (const c of cycles ?? []) {
      const k = c.dataset_name || "(unknown)";
      const arr = m.get(k) ?? [];
      arr.push(c);
      m.set(k, arr);
    }
    for (const arr of m.values()) {
      arr.sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
    }
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [cycles]);
  return (
    <label className={`compare-picker${disabled ? " disabled" : ""}`}>
      <span className="compare-picker-label">Slot {label}</span>
      <select
        value={selected ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
        disabled={disabled || !cycles || cycles.length === 0}
        aria-label={`Compare campaign slot ${label}`}
      >
        <option value="">— pick a campaign —</option>
        {groups.map(([proj, list]) => (
          <optgroup key={proj} label={proj}>
            {list.map((c) => (
              <option key={c.cycle_id} value={c.cycle_id}>
                {c.cycle_id.length > 26
                  ? `${c.cycle_id.slice(0, 18)}…${c.cycle_id.slice(-4)}`
                  : c.cycle_id}
                {" · "}
                best {c.best_accuracy == null ? "—" : `${(c.best_accuracy * 100).toFixed(0)}%`}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </label>
  );
}

function SummaryCard({ side, loaded }: { side: string; loaded: LoadedCampaign }) {
  const { cycle, detail } = loaded;
  const delta =
    detail.best_accuracy != null && detail.origin_accuracy != null
      ? detail.best_accuracy - detail.origin_accuracy
      : null;
  return (
    <div className="compare-card">
      <div className="compare-card-head">
        <span className="compare-card-side">Slot {side}</span>
        <span className="compare-card-status">{detail.status}</span>
      </div>
      <div className="compare-card-id" title={cycle.cycle_id}>{cycle.cycle_id}</div>
      <div className="compare-card-meta">
        {cycle.dataset_name || "—"} · {detail.n_rounds} rounds
      </div>
      <div className="compare-card-stats">
        <div>
          <div className="compare-card-val">{fmtPct(detail.best_accuracy)}</div>
          <div className="compare-card-lbl">best</div>
        </div>
        <div>
          <div className="compare-card-val">{fmtPct(detail.origin_accuracy)}</div>
          <div className="compare-card-lbl">origin</div>
        </div>
        <div>
          <div className={`compare-card-val ${delta != null && delta > 0 ? "up" : delta != null && delta < 0 ? "down" : ""}`}>
            {delta == null ? "—" : `${delta > 0 ? "+" : ""}${(delta * 100).toFixed(1)}pp`}
          </div>
          <div className="compare-card-lbl">Δ</div>
        </div>
      </div>
    </div>
  );
}

function DeltaCard({
  label,
  left,
  right,
  format,
}: {
  label: string;
  left: number | null;
  right: number | null;
  format: (v: number | null) => string;
}) {
  return (
    <div className="compare-delta-card">
      <div className="compare-delta-lbl">{label}</div>
      <div className="compare-delta-row-vals">
        <span>{format(left)}</span>
        <span className="compare-delta-arrow">→</span>
        <span>{format(right)}</span>
        <span className="compare-delta-diff">
          {typeof left === "number" && typeof right === "number"
            ? label === "Rounds"
              ? right - left > 0
                ? `(+${right - left})`
                : right - left < 0
                  ? `(${right - left})`
                  : "(=)"
              : fmtDelta(left, right)
            : ""}
        </span>
      </div>
    </div>
  );
}

function TrajectoryChart({
  left,
  right,
  themeKey,
}: {
  left: LoadedCampaign;
  right: LoadedCampaign;
  themeKey: string;
}) {
  const leftPts = trajectory(left.detail);
  const rightPts = trajectory(right.detail);
  const maxX = Math.max(
    ...leftPts.map((p) => p.x),
    ...rightPts.map((p) => p.x),
    1,
  );
  const labels = Array.from({ length: maxX + 1 }, (_, i) => `R${i}`);
  const pad = (pts: { x: number; y: number }[]) =>
    labels.map((_, i) => pts.find((p) => p.x === i)?.y ?? null);
  const data: ChartData<"line"> = {
    labels,
    datasets: [
      {
        label: "A",
        data: pad(leftPts),
        borderColor: "var(--color-accent)",
        backgroundColor: "transparent",
        spanGaps: true,
        tension: 0.2,
      },
      {
        label: "B",
        data: pad(rightPts),
        borderColor: "var(--color-selection)",
        backgroundColor: "transparent",
        spanGaps: true,
        tension: 0.2,
      },
    ],
  };
  const options: ChartOptions<"line"> = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: true } },
    scales: { y: { min: 0, max: 1, ticks: { callback: (v) => `${Math.round(Number(v) * 100)}%` } } },
  };
  return (
    <div className="compare-chart">
      <div className="compare-chart-title">Best-of-round accuracy</div>
      <div style={{ position: "relative", height: 280 }}>
        <Line key={themeKey} data={data} options={options} />
      </div>
    </div>
  );
}
