"use client";
// Walks the served `nests` chain. Depth is data, not code. Starts BELOW the dataset
// `ConnectorProvider` already holds, so the first hop is not re-fetched.

import { useEffect, useState } from "react";
import { fetchDatasetPipeline } from "@/lib/api";
import { failureKind } from "@/lib/api/client";
import type {
  DatasetPipelineResponse,
  NestedPipelineRef,
  NodeConfigParam,
  NodeReach,
  PipelineView,
} from "@/lib/api";
import type { PipelineStatus } from "@/lib/types";

export interface NestedLayer {
  dataset: string;
  connector: string | null;
  view: PipelineView | null;
  schema: Record<string, NodeConfigParam[]> | null;
  // Null while the level is only a pointer: unknown, which is how an unread node draws.
  reach: Record<string, NodeReach> | null;
  nestsNode: string | null;
  // `loading` while only the POINTER has arrived: a level exists once something names it.
  status: PipelineStatus;
}

const MAX_DEPTH = 6;

export interface NestedPipelines {
  layers: NestedLayer[];
  // Rendered: a truncated recursion must not look finished. In-flight is the layer's `status`.
  truncated: string | null;
}

const EMPTY: NestedPipelines = { layers: [], truncated: null };

export function useNestedPipelines(
  root: NestedPipelineRef | null,
  enabled: boolean,
): NestedPipelines {
  const [state, setState] = useState<NestedPipelines>(EMPTY);
  const key = enabled && root ? `${root.node}>${root.dataset}` : null;
  const [loadedKey, setLoadedKey] = useState<string | null>(null);

  useEffect(() => {
    if (!key || !root) return;
    let cancelled = false;
    (async () => {
      const layers: NestedLayer[] = [];
      const seen = new Set<string>();
      let next: NestedPipelineRef | null = root;
      let truncated: string | null = null;
      while (next) {
        if (seen.has(next.dataset)) {
          truncated = `${next.dataset} nests itself`;
          break;
        }
        if (layers.length >= MAX_DEPTH) {
          truncated = `stopped at ${MAX_DEPTH} layers`;
          break;
        }
        seen.add(next.dataset);
        let resp: DatasetPipelineResponse;
        try {
          resp = await fetchDatasetPipeline(next.dataset);
        } catch (e) {
          truncated = `${next.dataset} unavailable (${failureKind(e)})`;
          break;
        }
        if (cancelled) return;
        const onward = resp?.nests ?? null;
        layers.push({
          dataset: next.dataset,
          connector: resp?.connector ?? null,
          view: resp?.view ?? null,
          schema: resp?.node_config_schema ?? null,
          reach: resp?.reach ?? null,
          nestsNode: onward?.node ?? null,
          status: "ok",
        });
        next = onward;
      }
      if (!cancelled) {
        setState({ layers, truncated });
        setLoadedKey(key);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [key, root]);

  if (!key || !root) return EMPTY;
  if (loadedKey === key) return state;
  // Publish the named level now: zero layers would make the caller's level innermost for a frame.
  return {
    layers: [
      {
        dataset: root.dataset,
        connector: null,
        view: null,
        schema: null,
        reach: null,
        nestsNode: null,
        status: "loading",
      },
    ],
    truncated: null,
  };
}
