"use client";

import { useEffect, useState } from "react";
import {
  fetchDatasetIndex,
  fetchOrigins,
  type DatasetIndexEntry,
  type OriginEntry,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// What a campaign can be started FROM. Every ingest surface reads this one fetch: two
// fetchers are two answers to "what is in my collection".
export type CollectionState =
  | { kind: "loading" }
  | { kind: "needsAuth" }
  | { kind: "ready"; origins: OriginEntry[]; entries: DatasetIndexEntry[] }
  | { kind: "error" };

export function useCollection(): CollectionState {
  const { status } = useAuth();
  // An anon visitor fires nothing, so the protected read never 401s into the UI (I1/I5).
  const resting = (): CollectionState =>
    status === "authed" || status === "loading" ? { kind: "loading" } : { kind: "needsAuth" };
  const [state, setState] = useState<CollectionState>(resting);

  const [prevStatus, setPrevStatus] = useState(status);
  if (status !== prevStatus) {
    setPrevStatus(status);
    setState(resting());
  }

  useEffect(() => {
    if (status !== "authed") return;
    let cancelled = false;
    Promise.all([fetchDatasetIndex(), fetchOrigins()])
      .then(([datasets, origins]) => {
        if (!cancelled)
          setState({ kind: "ready", origins: origins.origins, entries: datasets.datasets });
      })
      .catch(() => {
        if (!cancelled) setState({ kind: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [status]);

  return state;
}
