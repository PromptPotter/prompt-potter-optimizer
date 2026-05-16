"use client";
import { useMemo, useState } from "react";
import { type DatasetItem } from "@/lib/api";
import { parseSampleLine } from "@/lib/sample-line";
import { liveL1Candidates, useCycleStream, type DashboardSnapshot } from "@/lib/poll";
import { HardSamplesTable } from "./HardSamplesTable";

interface Props {
  dash: DashboardSnapshot | null;
  dashRound: number | null;
  datasetName: string | null;
  datasetItems: DatasetItem[];
  datasetTrainCount: number;
  datasetTestCount: number;
}

interface Measurement {
  hit: boolean;
  // Ordering key: round * 1000 + candidate idx → stable chronological sort.
  ord: number;
}

interface RoundDoc {
  round: number;
  scoreboard?: { candidate_id?: string }[];
  all_candidate_results?: Record<string, { sample_id: number; hit?: boolean }[]>;
}

// Fold in live mid-round measurements that haven't been written to a round
// file yet. Live samples are compact strings ("0.0s #000 HIT ..."); the
// parser yields idx + status. We append them as the highest ord so they
// sit at the right edge of each row.
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
        // ``sampleId`` is the dataset id — what the heatmap rows are
        // keyed by. ``idx`` (qi) is iteration position and only matches
        // sample_id when the loop runs in dataset order, so we strictly
        // require sampleId here. Old-format lines without ``sid:`` just
        // skip — the round-complete flush will fill them in.
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
      const ord = round * 1000 + ci;
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
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const { rounds: historyDocs } = useCycleStream();
  const rounds: RoundDoc[] = useMemo(() => {
    const out: RoundDoc[] = [];
    for (const d of historyDocs) {
      if (typeof d.round !== "number") continue;
      out.push({
        round: d.round,
        scoreboard: d.scoreboard as { candidate_id?: string }[] | undefined,
        all_candidate_results:
          d.all_candidate_results as Record<string, { sample_id: number; hit?: boolean }[]> | undefined,
      });
    }
    return out;
  }, [historyDocs]);

  // Aggregate per-sample measurement history: rows = samples, columns =
  // chronological candidate measurements. Ordering is round * 1000 + the
  // candidate's scoreboard index so the strip reads left-to-right in time.
  const perSample = useMemo(() => {
    const out = new Map<number, Measurement[]>();
    for (const r of rounds) {
      const scoreboard = r.scoreboard ?? [];
      const acr = r.all_candidate_results ?? {};
      // Map candidate_id → scoreboard idx so order in the row matches
      // the round's ranking. Unknown ids fall to the end (idx = 99).
      const idxOf = new Map<string, number>();
      scoreboard.forEach((c, i) => {
        if (c.candidate_id) idxOf.set(c.candidate_id, i);
      });
      for (const [candId, results] of Object.entries(acr)) {
        const ci = idxOf.get(candId) ?? 99;
        const ord = (r.round ?? 0) * 1000 + ci;
        for (const s of results) {
          if (typeof s.sample_id !== "number" || typeof s.hit !== "boolean") continue;
          if (!out.has(s.sample_id)) out.set(s.sample_id, []);
          out.get(s.sample_id)!.push({ hit: s.hit, ord });
        }
      }
    }
    // Fold in live mid-round
    const live = liveMeasurements(dash, dashRound);
    for (const [sid, ms] of live) {
      if (!out.has(sid)) out.set(sid, []);
      out.get(sid)!.push(...ms);
    }
    // Sort each row by ord and de-dupe (live can overlap with the round
    // file once it lands) on (ord, hit) pairs.
    for (const ms of out.values()) {
      ms.sort((a, b) => a.ord - b.ord);
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
  }, [rounds, dash, dashRound]);

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
