"use client";
import { useEffect, useRef, useState } from "react";
import { cyclePathUrl } from "@/lib/api";
import { encodeCyclePath, pathLeaf, type CyclePath } from "@/lib/ids";
import { useWorkspace } from "@/lib/workspace";
import type { ActivityState, ProjectionEnvelope } from "@/lib/api/types";

interface CycleEventsState extends ActivityState {
  connected: boolean;
}

const NO_ACTIVITY: ActivityState = { status: null, notices: [], decision: null };

export function useCycleEvents(path: CyclePath | null): CycleEventsState {
  const [activity, setActivity] = useState<ActivityState>(NO_ACTIVITY);
  const [connected, setConnected] = useState(false);

  const key = path ? encodeCyclePath(path) : "";
  const [prevKey, setPrevKey] = useState(key);
  if (key !== prevKey) {
    setPrevKey(key);
    setActivity(NO_ACTIVITY);
    setConnected(false);
  }
  // The effect keys on the stable `key` string, not the path array's per-render identity.
  const pathRef = useRef<CyclePath | null>(path);
  useEffect(() => {
    pathRef.current = path;
  });

  const goneAddress = useWorkspace().goneAddress;
  const addressGone = goneAddress !== null && goneAddress === key;

  useEffect(() => {
    const p = pathRef.current;
    if (!p || addressGone) return;
    // A frame queued across a reconnect would paint and linger: this feed never re-validates.
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
      setActivity(env.activity);
    };

    return () => es.close();
  }, [key, addressGone]);

  return { ...activity, connected };
}
