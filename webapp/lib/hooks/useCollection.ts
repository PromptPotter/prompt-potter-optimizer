"use client";

import { useMemo } from "react";
import {
  datasetIndexRead,
  originsRead,
  type DatasetIndexEntry,
  type OriginEntry,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useRead } from "./useRead";

export type CollectionState =
  | { kind: "loading" }
  | { kind: "needsAuth" }
  | { kind: "ready"; origins: OriginEntry[]; entries: DatasetIndexEntry[] }
  | { kind: "error" };

export function useCollection(): CollectionState {
  const { status } = useAuth();
  const datasets = useRead(datasetIndexRead(), { auth: true });
  const origins = useRead(originsRead(), { auth: true });
  return useMemo((): CollectionState => {
    if (status === "unauthed") return { kind: "needsAuth" };
    if (datasets.status === "failed" || origins.status === "failed") return { kind: "error" };
    if (datasets.status !== "ready" || origins.status !== "ready") return { kind: "loading" };
    return { kind: "ready", origins: origins.data.origins, entries: datasets.data.datasets };
  }, [status, datasets, origins]);
}
