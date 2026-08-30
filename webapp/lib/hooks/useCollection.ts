"use client";

import { useEffect, useState } from "react";
import {
  fetchDatasetIndex,
  fetchOrigins,
  type DatasetIndexEntry,
  type OriginEntry,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// What a campaign can be started FROM: the origins this identity may reuse and
// the datasets it may make a new origin out of. One fetch for both ingest
// surfaces — the chat tab's entry list and the New campaign modal's — because
// two fetchers is two answers to "what is in my collection", and the one that
// only ran when a modal opened is why the chat tab could not offer the list at
// all.
export type CollectionState =
  | { kind: "loading" }
  | { kind: "needsAuth" }
  | { kind: "ready"; origins: OriginEntry[]; entries: DatasetIndexEntry[] }
  | { kind: "error" };

export function useCollection(): CollectionState {
  const { status } = useAuth();
  // Resting state is chosen by auth: a confirmed (or still resolving) session
  // waits on the fetch below; an anon visitor is told to sign in and fires
  // nothing, so the protected read never 401s into the UI
  // (frontend-surface-contract.md § I1/I5).
  const resting = (): CollectionState =>
    status === "authed" || status === "loading" ? { kind: "loading" } : { kind: "needsAuth" };
  const [state, setState] = useState<CollectionState>(resting);

  // Auth settling is an identity change, so the resting state is re-derived in
  // render phase rather than from the effect below — the reset then commits with
  // the same frame and no stale answer is painted (webapp/CLAUDE.md "State reset
  // on prop change").
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
