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
  PipelineView,
} from "@/lib/api";
import type { PipelineStatus } from "@/lib/types";

export interface NestedLayer {
  dataset: string;
  connector: string | null;
  view: PipelineView | null;
  schema: Record<string, NodeConfigParam[]> | null;
  // This layer's own nesting node, served, so a renderer never looks ahead to the next
  // layer to know whether to draw a handle.
  nestsNode: string | null;
  // `loading` while only the POINTER to this layer has arrived. The level exists from the
  // moment something names it, and only its content streams in — a layer that appears late
  // moves which one is innermost, and the stack re-lays every level out around it.
  status: PipelineStatus;
}

// Backstop for a dataset that transitively declares itself: a visible short read rather
// than a hung panel. Far above any real nesting.
const MAX_DEPTH = 6;

export interface NestedPipelines {
  layers: NestedLayer[];
  // Why the walk stopped early. A truncated recursion that looks finished is worse than a
  // short one, so this is rendered rather than left to the layer count. In-flight is NOT
  // reported here — it is the pending layer's own `status`, so the fact has one home.
  truncated: string | null;
}

const EMPTY: NestedPipelines = { layers: [], truncated: null };

export function useNestedPipelines(
  root: NestedPipelineRef | null,
  enabled: boolean,
): NestedPipelines {
  const [state, setState] = useState<NestedPipelines>(EMPTY);
  // Stamping the key onto the result is the pure-derivation half of webapp/CLAUDE.md
  // § State reset on prop change — no post-paint frame of the prior campaign's stack.
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
  // The pointer already NAMES the next level, so publish it now and let its content land.
  // Returning zero layers while the walk runs makes the CALLER's own level the innermost
  // one for a frame — it draws full size, with its own ends, and then re-lays out as a
  // container the moment the child arrives.
  return {
    layers: [
      {
        dataset: root.dataset,
        connector: null,
        view: null,
        schema: null,
        nestsNode: null,
        status: "loading",
      },
    ],
    truncated: null,
  };
}
