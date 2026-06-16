"use client";
import { useMemo, useState } from "react";
import {
  type DatasetItem,
  type HardSamplesScope,
  type MeasurementDot as ArchiveDot,
} from "@/lib/api";
import { parseSampleLine } from "@/lib/sample-line";
import { liveL1Candidates, type DashboardSnapshot } from "@/lib/poll";
import { HardSamplesTable } from "./HardSamplesTable";
import { SampleTrajectory, SampleTrajectoryMiniButton } from "./SampleTrajectory";
import { RunControlButton } from "@/components/dashboard/control/RunControlButton";
import { OriginGateModal } from "@/components/dashboard/control/OriginGateModal";
import { type MeasurementDot } from "./columns";
import { RotatePrompt } from "@/components/shell/RotatePrompt";

interface Props {
  // Run-control target — the play/pause/fork cluster sits beside the heat-map
  // + sample-trajectory toggles in this row, so the ids ride in here.
  campaignId: string | null;
  cycleId: string | null;
  dash: DashboardSnapshot | null;
  // Freshness gate — forwarded to HardSamplesTable so the row-scoring blink
  // stops when the optimizer process dies.
  isLive: boolean;
  dashRound: number | null;
  datasetName: string | null;
  datasetItems: DatasetItem[];
  datasetMeasuredCount: number;
  datasetUnmeasuredCount: number;
  datasetSplitTest: number | null;
  // Per-sample archive measurement series, fetched server-side from
  // /datasets/{name}/measurement-series. Scope toggle (this campaign vs
  // all campaigns on the dataset) is owned by AppShell and re-fetches
  // this map; the heat-map merges live mid-round samples on top.
  archivePerSample: Map<number, ArchiveDot[]>;
  // True while the displayed dataset slice is from a prior (unit, scope).
  datasetStale: boolean;
  hardSamplesScope: HardSamplesScope;
  onHardSamplesScopeChange: (s: HardSamplesScope) => void;
}

// Fold in live mid-round measurements that haven't landed in the archive
// yet. Live samples are compact strings ("0.0s #000 HIT ..."); the parser
// yields idx + status. They sit at the right edge of each row.
function liveMeasurements(
  dash: DashboardSnapshot | null,
  dashRound: number | null,
): Map<number, MeasurementDot[]> {
  const out = new Map<number, MeasurementDot[]>();
  const round = dashRound ?? 0;
  liveL1Candidates(dash).forEach((c, ci) => {
    for (const s of c.samples ?? []) {
      let sid: number | null = null;
      let hit: boolean | null = null;
      if (typeof s === "string") {
        const p = parseSampleLine(s);
        if (p.sampleId != null && p.status) {
          sid = p.sampleId;
          hit = p.status === "HIT";
        }
      } else if (s && typeof s === "object") {
        if (typeof s.sample_id === "number" && typeof s.hit === "boolean") {
          sid = s.sample_id;
          hit = s.hit;
        }
      }
      if (sid == null || hit == null) continue;
      // ``live/…`` prefix sorts after every archive ord (timestamps + 04d
      // round numbers start with digits) so in-flight cells land at the
      // right edge of the roster.
      const ord = `live/${round.toString().padStart(4, "0")}/${ci.toString().padStart(2, "0")}`;
      if (!out.has(sid)) out.set(sid, []);
      out.get(sid)!.push({ hit, ord });
    }
  });
  return out;
}

// Hard-samples heat-map. Collapsed: a compact resizable badge — one tile
// per sample in live Rasch difficulty order, green = mostly hit, red =
// mostly miss, dark = no measurements. Clicking the badge expands the full
// HardSamplesTable; the bottom-edge grip (hover to reveal) resizes it.
export function HardSamplesHeatmap({
  campaignId,
  cycleId,
  dash,
  isLive,
  dashRound,
  datasetName,
  datasetItems,
  datasetMeasuredCount,
  datasetUnmeasuredCount,
  datasetSplitTest,
  archivePerSample,
  datasetStale,
  hardSamplesScope,
  onHardSamplesScopeChange,
}: Props) {
  const [heatExpanded, setHeatExpanded] = useState(false);
  const [bankExpanded, setBankExpanded] = useState(false);

  // Merge archive series (scope-aware, server-sourced) with the live
  // mid-round samples (client-only, current cycle). De-dupe on (ord, hit)
  // in case a live measurement has already landed in the archive.
  const perSample = useMemo(() => {
    const out = new Map<number, MeasurementDot[]>();
    for (const [sid, ms] of archivePerSample) {
      out.set(
        sid,
        ms.map((m) => ({ hit: m.hit, ord: m.ord })),
      );
    }
    const live = liveMeasurements(dash, dashRound);
    for (const [sid, ms] of live) {
      if (!out.has(sid)) out.set(sid, []);
      out.get(sid)!.push(...ms);
    }
    for (const ms of out.values()) {
      ms.sort((a, b) => (a.ord < b.ord ? -1 : a.ord > b.ord ? 1 : 0));
      const seen = new Set<string>();
      let w = 0;
      for (let i = 0; i < ms.length; i++) {
        const k = `${ms[i].ord}:${ms[i].hit ? 1 : 0}`;
        if (seen.has(k)) continue;
        seen.add(k);
        ms[w++] = ms[i];
      }
      ms.length = w;
    }
    return out;
  }, [archivePerSample, dash, dashRound]);

  // Tile order — rank by Info gain (pick_score) descending, the same key
  // the hard-samples table sorts on, so the heatmap and table agree.
  // Contested samples first; always-hit / always-miss last; unmeasured
  // rows (null pick_score) last of all, in sample_id order.
  const sortedItems = useMemo(() => {
    return [...datasetItems].sort((a, b) => {
      const pa = a.pick_score;
      const pb = b.pick_score;
      if (pa === null || pb === null) {
        if (pa === pb) return a.sample_id - b.sample_id;
        return pa === null ? 1 : -1;
      }
      if (pa !== pb) return pb - pa;
      return a.sample_id - b.sample_id;
    });
  }, [datasetItems]);

  if (datasetItems.length === 0) return null;

  const totalHits = [...perSample.values()].reduce(
    (n, ms) => n + ms.filter((m) => m.hit).length,
    0,
  );
  const totalMeas = [...perSample.values()].reduce((n, ms) => n + ms.length, 0);

  const summary = `${datasetName ? `${datasetName} · ` : ""}${datasetItems.length} samples${
    totalMeas > 0 ? ` · ${totalHits}/${totalMeas} hit` : ""
  }`;

  return (
    <div className="hs-heat-wrap">
      <div className="hs-controls-row">
        <button
          type="button"
          className="hs-heat-mini-btn"
          onClick={() => setHeatExpanded((e) => !e)}
          aria-expanded={heatExpanded}
          aria-label={heatExpanded ? "Collapse sample heat-map" : `Expand sample heat-map. ${summary}.`}
          title={`${summary} — click to ${heatExpanded ? "collapse" : "expand"} · drag to resize`}
        >
          <span className="hs-heat-mini" aria-hidden="true">
            {sortedItems.map((it) => {
              const ms = perSample.get(it.sample_id);
              let cls: "hit" | "miss" | "none" = "none";
              if (ms && ms.length > 0) {
                const hits = ms.filter((m) => m.hit).length;
                cls = hits * 2 >= ms.length ? "hit" : "miss";
              }
              return (
                <span
                  key={it.sample_id}
                  className={`hs-heat-mini-cell ${cls}`}
                />
              );
            })}
          </span>
        </button>
        <SampleTrajectoryMiniButton
          expanded={bankExpanded}
          rounds={dash?.rounds ?? []}
          onToggle={() => setBankExpanded((e) => !e)}
        />
        <RunControlButton campaignId={campaignId} cycleId={cycleId} dash={dash} />
        <OriginGateModal campaignId={campaignId} cycleId={cycleId} dash={dash} />
      </div>
      {(bankExpanded || heatExpanded) && (
        <RotatePrompt surfaceName="The sample heat-map" skipRender>
          {bankExpanded && (
            <SampleTrajectory
              rounds={dash?.rounds ?? []}
              campaignId={campaignId}
              cycleId={cycleId}
            />
          )}
          {heatExpanded && (
            <div className="hs-expand-wrap">
              <HardSamplesTable
                dash={dash}
                isLive={isLive}
                perSample={perSample}
                datasetName={datasetName}
                datasetItems={datasetItems}
                datasetMeasuredCount={datasetMeasuredCount}
                datasetUnmeasuredCount={datasetUnmeasuredCount}
                datasetSplitTest={datasetSplitTest}
                datasetStale={datasetStale}
                scope={hardSamplesScope}
                onScopeChange={onHardSamplesScopeChange}
              />
            </div>
          )}
        </RotatePrompt>
      )}
    </div>
  );
}
