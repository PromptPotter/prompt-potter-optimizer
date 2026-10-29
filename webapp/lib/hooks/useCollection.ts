"use client";

import {
  datasetIndexRead,
  draftsRead,
  originsRead,
  type DatasetIndexEntry,
  type DraftSummary,
  type OriginEntry,
} from "@/lib/api";
import { useAuth } from "@/lib/state/auth";
import { useRead, type ReadFailure } from "./useRead";

export type CollectionState =
  | { kind: "loading" }
  | { kind: "needsAuth" }
  | {
      kind: "ready";
      drafts: DraftSummary[];
      origins: OriginEntry[];
      entries: DatasetIndexEntry[];
    }
  | { kind: "error"; failure: ReadFailure };

export function useCollection(): CollectionState {
  const { status } = useAuth();
  const datasets = useRead(datasetIndexRead(), { auth: true });
  const origins = useRead(originsRead(), { auth: true });
  const drafts = useRead(draftsRead(), { auth: true });
  if (status === "unauthed") return { kind: "needsAuth" };
  for (const read of [datasets, origins, drafts]) {
    if (read.status === "failed") return { kind: "error", failure: read.failure };
  }
  if (datasets.status !== "ready" || origins.status !== "ready" || drafts.status !== "ready")
    return { kind: "loading" };
  return {
    kind: "ready",
    drafts: drafts.data.drafts,
    origins: origins.data.origins,
    entries: datasets.data.datasets,
  };
}
