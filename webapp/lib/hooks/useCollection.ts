"use client";

import {
  datasetIndexRead,
  originsRead,
  type DatasetIndexEntry,
  type OriginEntry,
} from "@/lib/api";
import { useAuth } from "@/lib/state/auth";
import { useRead, type ReadFailure } from "./useRead";

// What a fresh campaign starts from. The open check-ins are not here: they are the registry's
// unstarted campaigns (`useRegistry().unstarted`), so a reader of them alone waits on neither read.
export type CollectionState =
  | { kind: "loading" }
  | { kind: "needsAuth" }
  | { kind: "ready"; origins: OriginEntry[]; entries: DatasetIndexEntry[] }
  | { kind: "error"; failure: ReadFailure };

export function useCollection(): CollectionState {
  const { status } = useAuth();
  const datasets = useRead(datasetIndexRead(), { auth: true });
  const origins = useRead(originsRead(), { auth: true });
  if (status === "unauthed") return { kind: "needsAuth" };
  for (const read of [datasets, origins]) {
    if (read.status === "failed") return { kind: "error", failure: read.failure };
  }
  if (datasets.status !== "ready" || origins.status !== "ready") return { kind: "loading" };
  return { kind: "ready", origins: origins.data.origins, entries: datasets.data.datasets };
}
