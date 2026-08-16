"use client";
import { useMemo, useState } from "react";
import {
  type DatasetItem,
  type HardSampleOrder,
  type HardSamplesScope,
  type SampleSeries,
} from "@/lib/api";
import type { SeriesTotals } from "@/lib/hooks/useDatasetPreview";
import { fmtPct0 } from "@/lib/format";
import { parseSampleLine } from "@/lib/sample-line";
import { liveL1Candidates, type DashboardSnapshot } from "@/lib/poll";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { HardSamplesTable } from "./HardSamplesTable";
import { SampleTrajectory, SampleTrajectoryMiniButton } from "./SampleTrajectory";
import { type HeatDot } from "./columns";
import { RotatePrompt } from "@/components/shell/RotatePrompt";

interface Props {
  datasetName: string | null;
  datasetItems: DatasetItem[];
  datasetMeasuredCount: number;
  datasetUnmeasuredCount: number;
  datasetSplitTest: number | null;
  // The key the server ranked by, off its echo. Not `order` — `sampleOrder` here is the
  // scoring WALK.
  datasetOrder: HardSampleOrder | null;
  hardSampleOrder: HardSampleOrder | null;
  onHardSampleOrderChange: (o: HardSampleOrder) => void;
  // Per-sample archive measurement series, fetched server-side from
  // /datasets/{name}/measurement-series. Scope toggle (this campaign vs
  // all campaigns on the dataset) is owned by AppShell and re-fetches
  // this map; the heat-map merges live mid-round samples on top.
  archivePerSample: Map<number, SampleSeries>;
  // Served roster-wide outcome totals for the scope in view. The headline reads
  // these rather than folding the strip's dots down: the live tail merged into
  // the strip is TEXT-reconstructed to 0/1 endpoints (see `liveMeasurements`),
  // so summing it would mix reconstructed endpoints into a graded rate.
  datasetTotals: SeriesTotals | null;
  // True while the displayed dataset slice is from a prior (unit, scope).
  datasetStale: boolean;
  // Set when the roster read failed for the unit in view. Distinct from an empty
  // roster: this panel must never answer a broken read with the same silence it
  // gives a genuinely empty one.
  datasetError: string | null;
  hardSamplesScope: HardSamplesScope;
  onHardSamplesScopeChange: (s: HardSamplesScope) => void;
}

// Fold in live mid-round measurements that haven't landed in the archive
// yet. Live samples are compact strings ("0.0s #000 HIT ..."); the parser
// yields idx + status. They sit at the right edge of each row.
function liveMeasurements(
  dash: DashboardSnapshot | null,
  dashRound: number | null,
): Map<number, HeatDot[]> {
  const out = new Map<number, HeatDot[]>();
  const round = dashRound ?? 0;
  liveL1Candidates(dash).forEach((c, ci) => {
    for (const s of c.samples ?? []) {
      let sid: number | null = null;
      let fitness: number | null = null;
      if (typeof s === "string") {
        // The live tape is TEXT, so it carries only the HIT/MISS mark the terminal
        // renders — the grade itself never survives the line. Reconstruct the two
        // endpoints; the dict branch below has the real number.
        const p = parseSampleLine(s);
        if (p.sampleId != null && p.status) {
          sid = p.sampleId;
          fitness = p.status === "HIT" ? 1 : 0;
        }
      } else if (s && typeof s === "object") {
        if (typeof s.sample_id === "number" && typeof s.fitness === "number") {
          sid = s.sample_id;
          fitness = s.fitness;
        }
      }
      if (sid == null || fitness == null) continue;
      // ``live/…`` prefix sorts after every archive ord (timestamps + 04d
      // round numbers start with digits) so in-flight cells land at the
      // right edge of the roster.
      const ord = `live/${round.toString().padStart(4, "0")}/${ci.toString().padStart(2, "0")}`;
      if (!out.has(sid)) out.set(sid, []);
      out.get(sid)!.push({ fitness, ord });
    }
  });
  return out;
}

// Hard-samples heat-map. Collapsed: a compact resizable badge — one tile
// per sample in live Rasch difficulty order, green = mostly hit, red =
// mostly miss, dark = no measurements. Clicking the badge expands the full
// HardSamplesTable; the bottom-edge grip (hover to reveal) resizes it.
export function HardSamplesHeatmap({
  datasetName,
  datasetItems,
  datasetMeasuredCount,
  datasetUnmeasuredCount,
  datasetSplitTest,
  datasetOrder,
  hardSampleOrder,
  onHardSampleOrderChange,
  archivePerSample,
  datasetTotals,
  datasetStale,
  datasetError,
  hardSamplesScope,
  onHardSamplesScopeChange,
}: Props) {
  // Live snapshot, self-sourced — feeds the live mid-round measurement merge.
  // The table, run-control, and trajectory children self-source their own
  // liveness/ids now, so nothing is threaded down from here.
  const { dash, dashRound } = useDashboard();
  const [heatExpanded, setHeatExpanded] = useState(false);
  const [bankExpanded, setBankExpanded] = useState(false);

  // Merge archive series (scope-aware, server-sourced) with the live
  // mid-round samples (client-only, current cycle). De-dupe on (ord, fitness)
  // in case a live measurement has already landed in the archive.
  const perSample = useMemo(() => {
    const out = new Map<number, HeatDot[]>();
    for (const [sid, s] of archivePerSample) {
      out.set(
        sid,
        s.measurements.map((m) => ({ fitness: m.fitness, ord: m.ord })),
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
      for (const m of ms) {
        const k = `${m.ord}:${m.fitness}`;
        if (seen.has(k)) continue;
        seen.add(k);
        ms[w++] = m;
      }
      ms.length = w;
    }
    return out;
  }, [archivePerSample, dash, dashRound]);

  // Tile order is the order `/preview` SERVED — `items[i].hard_sample_rank === i + 1`
  // (`datasets.py`), so `datasetItems` IS the ranking and nothing here arranges it. Two
  // hand-written comparators lived here and in the table; sorting on the served rank
  // instead only made the re-derivation agree with itself. An ordering is a score: the
  // fix is not to sort it correctly, it is not to sort it.

  // THREE facts, three sentences. A failed read and an empty roster were split
  // first (both used to `return null`, so a 404 was indistinguishable from a
  // collapsed panel); the third — STILL LOADING — stayed folded into "empty", and
  // that is the one an operator meets most. The roster is four `limit=1000` reads
  // awaited together, so the first load is genuinely slow, and for its whole
  // duration this panel asserted the campaign had no samples and hid its two
  // controls with it. `useDatasetPreview` already reports that state
  // (`isStale` with an empty slice, its comment: "show a loading affordance, not
  // blank chrome") — the claim was made over the top of the signal.
  if (datasetError) {
    return (
      <div className="hs-heat-wrap">
        <p className="hs-heat-empty" role="status">
          Couldn&rsquo;t read this campaign&rsquo;s samples
          {datasetName ? ` (${datasetName})` : ""}.
        </p>
      </div>
    );
  }
  if (datasetItems.length === 0) {
    return (
      <div className="hs-heat-wrap">
        <p className="hs-heat-empty" role="status">
          {datasetStale
            ? "Loading this campaign’s samples…"
            : "No samples on this campaign’s dataset yet."}
        </p>
      </div>
    );
  }

  // Mean fitness, not a hit rate: on a binary scorer the two are the same number
  // (the mean of 0/1 IS the hit rate), and on a graded one only this reports
  // anything — the hit ceiling is unreachable there, which is what made the old
  // "0/N hit" headline read as a dead pipeline on a working campaign.
  const outcome =
    datasetTotals && datasetTotals.mean_fitness != null
      ? ` · ${datasetTotals.total_measurements} measurements · ${fmtPct0(datasetTotals.mean_fitness)} mean fitness`
      : "";
  const summary = `${datasetName ? `${datasetName} · ` : ""}${datasetItems.length} samples${outcome}`;

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
            {datasetItems.map((it) => {
              const ms = perSample.get(it.sample_id);
              let cls: "hit" | "miss" | "none" = "none";
              if (ms && ms.length > 0) {
                const mean =
                  ms.reduce((k, m) => k + m.fitness, 0) / ms.length;
                cls = mean >= 0.5 ? "hit" : "miss";
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
      </div>
      {(bankExpanded || heatExpanded) && (
        <RotatePrompt surfaceName="The sample heat-map" skipRender>
          {bankExpanded && <SampleTrajectory rounds={dash?.rounds ?? []} />}
          {heatExpanded && (
            <div className="hs-expand-wrap">
              <HardSamplesTable
                perSample={perSample}
                servedSeries={archivePerSample}
                datasetName={datasetName}
                datasetItems={datasetItems}
                datasetMeasuredCount={datasetMeasuredCount}
                datasetUnmeasuredCount={datasetUnmeasuredCount}
                datasetSplitTest={datasetSplitTest}
                rankedBy={datasetOrder}
                rankedByPick={hardSampleOrder}
                onRankedByChange={onHardSampleOrderChange}
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
