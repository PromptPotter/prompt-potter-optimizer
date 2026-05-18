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

interface Props {
  dash: DashboardSnapshot | null;
  dashRound: number | null;
  datasetName: string | null;
  datasetItems: DatasetItem[];
  datasetTrainCount: number;
  datasetTestCount: number;
  // Per-sample archive measurement series, fetched server-side from
  // /datasets/{name}/measurement-series. Scope toggle (workspace vs
  // campaign) is owned by DashboardPane and re-fetches this map; the
  // heat-map merges live mid-round samples on top.
  archivePerSample: Map<number, ArchiveDot[]>;
  hardSamplesScope: HardSamplesScope;
  onHardSamplesScopeChange: (s: HardSamplesScope) => void;
}

interface Measurement {
  hit: boolean;
  // Stable composite key — server-derived for archive rows
  // ("created_at/run_id/idx") or client-derived for in-flight live samples
  // ("live/{round:04d}/{cand_idx:02d}"). Lex-sortable; the table aligns
  // rows on this key so equal ords share a column.
  ord: string;
}

// Fold in live mid-round measurements that haven't landed in the archive
// yet. Live samples are compact strings ("0.0s #000 HIT ..."); the parser
// yields idx + status. They sit at the right edge of each row.
function liveMeasurements(
  dash: DashboardSnapshot | null,
  dashRound: number | null,
): Map<number, Measurement[]> {
  const out = new Map<number, Measurement[]>();
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

export function HardSamplesHeatmap({
  dash,
  dashRound,
  datasetName,
  datasetItems,
  datasetTrainCount,
  datasetTestCount,
  archivePerSample,
  hardSamplesScope,
  onHardSamplesScopeChange,
}: Props) {
  const [expanded, setExpanded] = useState(false);

  // Merge archive series (scope-aware, server-sourced) with the live
  // mid-round samples (client-only, current cycle). De-dupe on (ord, hit)
  // in case a live measurement has already landed in the archive.
  const perSample = useMemo(() => {
    const out = new Map<number, Measurement[]>();
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
      {expanded ? (
        <div className="hs-expand-wrap">
          <button
            type="button"
            className="hs-heat-shrink-fab"
            onClick={() => setExpanded(false)}
            aria-expanded={true}
            title="Shrink"
            aria-label="Shrink heat-map"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
              <path d="M3 3 L9 9 M9 3 L3 9" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" fill="none" />
            </svg>
          </button>
          <HardSamplesTable
            dash={dash}
            perSample={perSample}
            datasetName={datasetName}
            datasetItems={datasetItems}
            datasetTrainCount={datasetTrainCount}
            datasetTestCount={datasetTestCount}
            scope={hardSamplesScope}
            onScopeChange={onHardSamplesScopeChange}
          />
        </div>
      ) : (
        <button
          type="button"
          className="hs-heat-mini-btn"
          onClick={() => setExpanded(true)}
          aria-expanded={false}
          aria-label={`Expand sample heat-map. ${summary}.`}
          title={summary}
        >
          <span className="hs-heat-mini" aria-hidden="true">
            {datasetItems.map((it) => {
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
      )}
    </div>
  );
}
