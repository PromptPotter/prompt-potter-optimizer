"use client";

import { useMemo } from "react";
import {
  datasetIndexRead,
  draftsRead,
  originsRead,
  type DatasetIndexEntry,
  type DraftSummary,
  type OriginEntry,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useRead } from "./useRead";

export type CollectionState =
  | { kind: "loading" }
  | { kind: "needsAuth" }
  | {
      kind: "ready";
      drafts: DraftSummary[];
      origins: OriginEntry[];
      entries: DatasetIndexEntry[];
    }
  | { kind: "error" };

export function useCollection(): CollectionState {
  const { status } = useAuth();
  const datasets = useRead(datasetIndexRead(), { auth: true });
  const origins = useRead(originsRead(), { auth: true });
  const drafts = useRead(draftsRead(), { auth: true });
  return useMemo((): CollectionState => {
    if (status === "unauthed") return { kind: "needsAuth" };
    if (datasets.status === "failed" || origins.status === "failed" || drafts.status === "failed")
      return { kind: "error" };
    if (datasets.status !== "ready" || origins.status !== "ready" || drafts.status !== "ready")
      return { kind: "loading" };
    return {
      kind: "ready",
      drafts: drafts.data.drafts,
      origins: origins.data.origins,
      entries: datasets.data.datasets,
    };
  }, [status, datasets, origins, drafts]);
}
