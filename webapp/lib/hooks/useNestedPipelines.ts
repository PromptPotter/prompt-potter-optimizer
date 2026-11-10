// Starts BELOW the dataset `useConnector` already reads, so the first hop is not re-fetched.

import { datasetPipelineRead, failureKind, operatorMessage } from "@/lib/api";
import { reportIncident } from "@/lib/diagnostics";
import { readThrough, type ReadDescriptor, type ReadResult } from "@/lib/read-cache";
import { useRead } from "./useRead";
import type {
  DatasetPipelineResponse,
  NestedPipelineRef,
  ServedParam,
  NodeReach,
  PipelineView,
} from "@/lib/api/types";

// One per dataset a level's cells measure, so a level holds several; `parent` is the enclosing layer's `key`.
interface NestedLayer {
  key: string;
  parent: string;
  depth: number;
  dataset: string;
  connector: string | null;
  view: PipelineView | null;
  schema: Record<string, ServedParam[]> | null;
  // Null is UNKNOWN, never shut.
  reach: Record<string, NodeReach> | null;
  nestsNode: string | null;
  read: ReadResult<unknown>;
}

const MAX_DEPTH = 6;

interface NestedPipelines {
  // Breadth-first: every layer follows the one it nests in.
  layers: NestedLayer[];
  truncated: string | null;
}

const EMPTY: NestedPipelines = { layers: [], truncated: null };

interface Pending {
  ref: NestedPipelineRef;
  parent: string;
  depth: number;
  // The datasets above it on its own branch: a sibling may name one, an ancestor may not.
  above: ReadonlySet<string>;
}

function pending(roots: NestedPipelineRef[], parent: string): Pending[] {
  return roots.map((ref) => ({ ref, parent, depth: 0, above: new Set<string>() }));
}

// The `parent` of every layer the campaign's own pipeline nests.
export const CAMPAIGN_LAYER = "campaign";

function nestedPipelinesRead(roots: NestedPipelineRef[]): ReadDescriptor<NestedPipelines> {
  return {
    id: `nested-pipelines\x1f${roots.map((r) => `${r.node}>${r.dataset}`).join("\x1e")}`,
    load: async (signal) => {
      const layers: NestedLayer[] = [];
      const queue = pending(roots, CAMPAIGN_LAYER);
      let truncated: string | null = null;
      for (let next = queue.shift(); next; next = queue.shift()) {
        const { ref, depth, above } = next;
        if (above.has(ref.dataset)) {
          truncated = `${ref.dataset} nests itself`;
          continue;
        }
        if (depth >= MAX_DEPTH) {
          truncated = `stopped at ${MAX_DEPTH} layers`;
          continue;
        }
        const key = `${next.parent}>${ref.dataset}`;
        let resp: DatasetPipelineResponse;
        try {
          resp = await readThrough(datasetPipelineRead(ref.dataset), signal);
        } catch (e) {
          if (signal.aborted) throw e;
          // The level stays in the stack, carrying its failure; what it nests is unknown.
          const kind = failureKind(e);
          reportIncident(e, { surface: "nested-pipelines", address: ref.dataset });
          layers.push({
            ...unread(ref, next.parent, depth),
            key,
            read: { status: "failed", failure: { kind, message: operatorMessage(e, kind) }, kept: null },
          });
          continue;
        }
        layers.push({
          key,
          parent: next.parent,
          depth,
          dataset: ref.dataset,
          connector: resp.connector,
          view: resp.view,
          schema: resp.node_config_schema,
          reach: resp.reach,
          nestsNode: resp.nests[0]?.node ?? null,
          read: { status: "ready", data: resp },
        });
        const below = new Set(above).add(ref.dataset);
        queue.push(...resp.nests.map((r) => ({ ref: r, parent: key, depth: depth + 1, above: below })));
      }
      return { kind: "ok", data: { layers, truncated }, validator: null };
    },
  };
}

export function useNestedPipelines(roots: NestedPipelineRef[], enabled: boolean): NestedPipelines {
  const read = useRead(enabled && roots.length > 0 ? nestedPipelinesRead(roots) : null);

  if (!enabled || roots.length === 0) return EMPTY;
  if (read.status === "ready") return read.data;
  // Publish the named level now: zero layers would make the caller's level innermost for a frame.
  return {
    layers: roots.map((ref) => unread(ref, CAMPAIGN_LAYER, 0)),
    truncated: null,
  };
}

function unread(ref: NestedPipelineRef, parent: string, depth: number): NestedLayer {
  return {
    key: `${parent}>${ref.dataset}`,
    parent,
    depth,
    dataset: ref.dataset,
    connector: null,
    view: null,
    schema: null,
    reach: null,
    nestsNode: null,
    read: { status: "loading", kept: null },
  };
}
