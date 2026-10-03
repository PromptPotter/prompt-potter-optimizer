"use client";
import { useEffect, useRef, useState } from "react";
import { cyclePathUrl } from "@/lib/api";
import { encodeCyclePath, pathLeaf, type CyclePath } from "@/lib/ids";
import { useWorkspace } from "@/lib/workspace";
import {
  projectionToActivity,
  sampleOrderFrom,
  sampleScoredCandidate,
  snapshotToActivity,
  type ActivityItem,
} from "./activity";
import type { ProjectionEnvelope } from "@/lib/api/types";

// The one live channel: tails the cycle ledger over SSE. Summaries persist by id; the call trail
// is capped and cleared at every candidate/round boundary.

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

interface CycleEventsState {
  activity: ActivityItem[];
  progress: ActivityItem | null;
  connected: boolean;
  sampleOrder: number[] | null;
}

export function useCycleEvents(path: CyclePath | null): CycleEventsState {
  const [summaries, setSummaries] = useState<ActivityItem[]>([]);
  const [trail, setTrail] = useState<ActivityItem[]>([]);
  const [progress, setProgress] = useState<ActivityItem | null>(null);
  const [connected, setConnected] = useState(false);
  const [sampleOrder, setSampleOrder] = useState<number[] | null>(null);

  const key = path ? encodeCyclePath(path) : "";
  const [prevKey, setPrevKey] = useState(key);
  if (key !== prevKey) {
    setPrevKey(key);
    setSummaries([]);
    setTrail([]);
    setProgress(null);
    setConnected(false);
    setSampleOrder(null);
  }
  // The effect keys on the stable `key` string, not the path array's per-render identity.
  const pathRef = useRef<CyclePath | null>(path);
  useEffect(() => {
    pathRef.current = path;
  });

  // `EventSource` cannot see a status and reconnects a 404 forever, so this subscribes only while
  // the workspace (whose dashboard read CAN see it) holds the address live.
  const goneAddress = useWorkspace().goneAddress;
  const addressGone = goneAddress !== null && goneAddress === key;

  useEffect(() => {
    const p = pathRef.current;
    if (!p || addressGone) return;
    // A frame queued across a teardown/reconnect would otherwise upsert and linger, since this feed
    // never re-validates; `poll.tsx` drops mismatches the same way.
    const expectedCycleId = pathLeaf(p).cycleId;
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
      // Tolerates a missing stamp, so an unstamped snapshot frame is never eaten.
      if (env.cycle_id && env.cycle_id !== expectedCycleId) return;
      if (env.kind === "stream_snapshot") {
        setSummaries(snapshotToActivity(env.payload));
        setTrail([]);
        setProgress(null);
        // A kept order could point at a walk that is no longer running.
        setSampleOrder(null);
        return;
      }
      const order = sampleOrderFrom(env);
      if (order) {
        setSampleOrder(order);
        return; // state, not an item — nothing to append
      }
      const candUpd = sampleScoredCandidate(env);
      if (candUpd) setSummaries((prev) => upsert(prev, candUpd));
      const item = projectionToActivity(env);
      if (!item) return;
      if (item.kind === "progress") {
        setProgress(item);
        return;
      }
      if (item.kind === "candidate" || item.kind === "round") {
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
  }, [key, addressGone]);

  return { activity: [...summaries, ...trail], progress, connected, sampleOrder };
}
