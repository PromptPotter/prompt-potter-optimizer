"use client";
import { useEffect, useRef, useState } from "react";
import { cyclePathUrl } from "@/lib/api";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";
import {
  projectionToActivity,
  sampleScoredCandidate,
  snapshotToActivity,
  type ActivityItem,
  type ProjectionEnvelope,
} from "./activity";

// The webapp's first SSE consumer. Subscribes to the cycle event stream
// `GET /campaigns/{c}/cycles/{cy}/events:subscribe`
// (`presentation/api/routers/campaigns/events.py`), which tails the on-disk
// ledger cross-process — so this works no matter which process runs the
// campaign (the API server, the CLI, a spawned runner).
//
// The feed is two layers so it stays short:
//   • summaries — the persistent lineage + control log (round / candidate /
//     warning / error / merge), one line each, upserted by stable id.
//   • trail — the CURRENT segment's verbose optimizer calls (running / done),
//     capped and cleared at every candidate/round boundary. So a candidate's
//     ↻/✓ call-trail collapses to its summary line once it's done, and the next
//     candidate's work shows — the chat doesn't grow unbounded.
// `progress` is the single transient "scoring i/n" chip; it clears on a boundary.
//
// The leading `stream_snapshot` frame paints the state-so-far from the cycle's
// dashboard.json (completed rounds + the in-flight current round), so opening
// mid-run shows the candidate being scored rather than the last finished round.
// The endpoint 404s only for an unknown cycle; a reconnect re-paints from a
// fresh snapshot, so the feed self-heals without doubling.

const TRAIL_MAX = 12; // the current segment's calls only — older ones collapse
const SUMMARY_KINDS = new Set<ActivityItem["kind"]>([
  "round",
  "candidate",
  "warning",
  "error",
  "merge",
]);

function upsert(prev: ActivityItem[], item: ActivityItem): ActivityItem[] {
  const idx = prev.findIndex((x) => x.id === item.id);
  if (idx >= 0) {
    const next = prev.slice();
    next[idx] = item;
    return next;
  }
  return [...prev, item];
}

export interface CycleEventsState {
  // Combined feed: persistent summaries followed by the current call-trail.
  activity: ActivityItem[];
  // The single transient "scoring i/n" chip — replaced, never appended.
  progress: ActivityItem | null;
  connected: boolean;
}

// Addressed by the viewed CYCLE PATH, not bare ids: the feed follows the LEAF hop
// the dashboard shows, so an L4 inner drill-in tails the INNER cycle's ledger (via
// `?descend=`) instead of the outer root's — each inner campaign shows its own
// activity, not the shared outer candidate cards. At depth 1 the URL is
// byte-identical to a plain per-cycle subscribe.
export function useCycleEvents(path: CyclePath | null): CycleEventsState {
  const [summaries, setSummaries] = useState<ActivityItem[]>([]);
  const [trail, setTrail] = useState<ActivityItem[]>([]);
  const [progress, setProgress] = useState<ActivityItem | null>(null);
  const [connected, setConnected] = useState(false);

  // Render-phase guarded reset on unit switch (webapp/CLAUDE.md § State reset on
  // prop change): clear the prior cycle's feed in the same commit, no stale frame.
  // Keyed on the ENCODED path so a drill-in into/out of an inner loop re-subscribes.
  const key = path ? encodeCyclePath(path) : "";
  const [prevKey, setPrevKey] = useState(key);
  if (key !== prevKey) {
    setPrevKey(key);
    setSummaries([]);
    setTrail([]);
    setProgress(null);
    setConnected(false);
  }
  // Keep the current path for the subscribe without depending on the array's
  // per-render identity — the effect keys on the stable `key` string alone.
  const pathRef = useRef<CyclePath | null>(path);
  useEffect(() => {
    pathRef.current = path;
  });

  useEffect(() => {
    const p = pathRef.current;
    if (!p) return;
    const es = new EventSource(cyclePathUrl(p, "/events:subscribe"), {
      withCredentials: true,
    });

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false); // EventSource auto-reconnects; re-paints from a fresh snapshot

    es.onmessage = (ev) => {
      let env: ProjectionEnvelope;
      try {
        env = JSON.parse(ev.data) as ProjectionEnvelope;
      } catch {
        return;
      }
      if (env.kind === "stream_snapshot") {
        setSummaries(snapshotToActivity(env.payload ?? {}));
        setTrail([]);
        setProgress(null);
        return;
      }
      // Live candidate fitness from the running composite the scorer rides on
      // each sample — a plain upsert (no boundary clear), so the candidate's
      // number moves in real time alongside the progress chip.
      const candUpd = sampleScoredCandidate(env);
      if (candUpd) setSummaries((prev) => upsert(prev, candUpd));
      const item = projectionToActivity(env);
      if (!item) return;
      if (item.kind === "progress") {
        setProgress(item);
        return;
      }
      if (item.kind === "candidate" || item.kind === "round") {
        // A new candidate/round boundary — collapse the prior segment's call
        // trail + progress, keep only the summary line.
        setTrail([]);
        setProgress(null);
        setSummaries((prev) => upsert(prev, item));
      } else if (SUMMARY_KINDS.has(item.kind)) {
        setSummaries((prev) => upsert(prev, item)); // warning / error / merge — persist
      } else {
        setTrail((prev) => [...prev, item].slice(-TRAIL_MAX)); // running / done
      }
    };

    return () => es.close();
  }, [key]);

  return { activity: [...summaries, ...trail], progress, connected };
}
