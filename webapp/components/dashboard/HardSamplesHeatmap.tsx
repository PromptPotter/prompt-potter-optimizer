"use client";
import { useEffect, useMemo, useState } from "react";
import {
  fetchActiveDatasetName,
  fetchDatasetPreview,
  type DatasetItem,
} from "@/lib/api";
import { parseSampleLine } from "@/lib/sample-line";
import { roundOf, type DashboardSnapshot } from "@/lib/poll";
import { useRoundHistory } from "@/lib/use-round-history";
import { HardSamplesTable } from "./HardSamplesTable";

const MAX_FETCH = 1000;

interface Props {
  cycleId: string | null;
  dash: DashboardSnapshot | null;
  refreshKey: number;
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
function liveMeasurements(dash: DashboardSnapshot | null): Map<number, Measurement[]> {
  const out = new Map<number, Measurement[]>();
  if (!dash) return out;
  const round = roundOf(dash) ?? 0;
  const nodes = (dash.current_round?.nodes ?? {}) as Record<string, unknown>;
  const l1Score = nodes.l1_score as { output?: { candidates?: unknown[] } } | undefined;
  const cands = l1Score?.output?.candidates ?? [];
  cands.forEach((c, ci) => {
    const samples = (c as { samples?: unknown[] }).samples ?? [];
    for (const s of samples) {
      let sid: number | null = null;
      let hit: boolean | null = null;
      if (typeof s === "string") {
        const p = parseSampleLine(s);
        if (p.idx != null && p.status) {
          sid = p.idx;
          hit = p.status === "HIT";
        }
      } else if (s && typeof s === "object") {
        const r = s as Record<string, unknown>;
        if (typeof r.sample_id === "number" && typeof r.hit === "boolean") {
          sid = r.sample_id;
          hit = r.hit;
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

export function HardSamplesHeatmap({ cycleId, dash, refreshKey }: Props) {
  const [items, setItems] = useState<DatasetItem[]>([]);
  const [datasetName, setDatasetName] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const historyDocs = useRoundHistory(cycleId, refreshKey);
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

  useEffect(() => {
    if (!cycleId) return;
    let cancelled = false;
    const ac = new AbortController();
    (async () => {
      try {
        const name = await fetchActiveDatasetName(cycleId, ac.signal);
        if (cancelled || !name) return;
        setDatasetName(name);
        const preview = await fetchDatasetPreview(name, MAX_FETCH, ac.signal);
        if (cancelled) return;
        setItems(preview.items);
      } catch {
        // load may race with cycle-mint
      }
    })();
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [cycleId]);

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
    const live = liveMeasurements(dash);
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
  }, [rounds, dash]);

  if (items.length === 0) return null;

  const totalHits = [...perSample.values()].reduce(
    (n, ms) => n + ms.filter((m) => m.hit).length,
    0,
  );
  const totalMeas = [...perSample.values()].reduce((n, ms) => n + ms.length, 0);

  const summary = `${datasetName ? `${datasetName} · ` : ""}${items.length} samples${
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
            cycleId={cycleId}
            dash={dash}
            perSample={perSample}
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
            {items.map((it) => {
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
