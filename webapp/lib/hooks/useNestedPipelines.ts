"use client";
// Starts BELOW the dataset `ConnectorProvider` already holds, so the first hop is not re-fetched.

import { datasetPipelineRead } from "@/lib/api";
import { failureKind } from "@/lib/api/client";
import { readThrough, type ReadDescriptor } from "@/lib/read-cache";
import { useRead } from "./useRead";
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
  // Null is UNKNOWN, never shut.
  reach: Record<string, NodeReach> | null;
  nestsNode: string | null;
  status: PipelineStatus;
}

const MAX_DEPTH = 6;

export interface NestedPipelines {
  layers: NestedLayer[];
  truncated: string | null;
}

const EMPTY: NestedPipelines = { layers: [], truncated: null };

function nestedPipelinesRead(root: NestedPipelineRef): ReadDescriptor<NestedPipelines> {
  return {
    id: `nested-pipelines\x1f${root.node}>${root.dataset}`,
    load: async (signal) => {
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
          const level = datasetPipelineRead(next.dataset);
          resp = await readThrough(level.id, level.load, signal);
        } catch (e) {
          if (signal.aborted) throw e;
          truncated = `${next.dataset} unavailable (${failureKind(e)})`;
          break;
        }
        layers.push({
          dataset: next.dataset,
          connector: resp.connector,
          view: resp.view,
          schema: resp.node_config_schema,
          reach: resp.reach,
          nestsNode: resp.nests?.node ?? null,
          status: "ok",
        });
        next = resp.nests;
      }
      return { kind: "ok", data: { layers, truncated }, validator: null };
    },
  };
}

export function useNestedPipelines(
  root: NestedPipelineRef | null,
  enabled: boolean,
): NestedPipelines {
  const read = useRead(enabled && root ? nestedPipelinesRead(root) : null);

  if (!enabled || !root) return EMPTY;
  if (read.status === "ready") return read.data;
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
