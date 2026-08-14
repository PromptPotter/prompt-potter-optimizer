"use client";
// Walks the served `nests` chain. Depth is data, not code. Starts BELOW the dataset
// `ConnectorProvider` already holds, so the first hop is not re-fetched.

import { useEffect, useState } from "react";
import { fetchDatasetPipeline } from "@/lib/api";
import { failureKind } from "@/lib/api/client";
import type { NestedPipelineRef, NodeConfigParam } from "@/lib/api";
import type { PipelineView } from "@/components/workflow";

export interface NestedLayer {
  dataset: string;
  connector: string | null;
  view: PipelineView | null;
  schema: Record<string, NodeConfigParam[]> | null;
  // This layer's own nesting node, served, so a renderer never looks ahead to the next
  // layer to know whether to draw a handle.
  nestsNode: string | null;
}

// Backstop for a dataset that transitively declares itself: a visible short read rather
// than a hung panel. Far above any real nesting.
const MAX_DEPTH = 6;

export interface NestedPipelines {
  layers: NestedLayer[];
  loading: boolean;
  // Why the walk stopped early. A truncated recursion that looks finished is worse than a
  // short one, so this is rendered rather than left to the layer count.
  truncated: string | null;
}

const EMPTY: NestedPipelines = { layers: [], loading: false, truncated: null };

type PipelineWire = {
  view?: PipelineView | null;
  connector?: string | null;
  node_config_schema?: Record<string, NodeConfigParam[]> | null;
  nests?: NestedPipelineRef | null;
};

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
        let resp: PipelineWire;
        try {
          resp = (await fetchDatasetPipeline(next.dataset)) as PipelineWire;
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
        });
        next = onward;
      }
      if (!cancelled) {
        setState({ layers, loading: false, truncated });
        setLoadedKey(key);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [key, root]);

  if (!key) return EMPTY;
  return loadedKey === key ? state : { ...EMPTY, loading: true };
}
