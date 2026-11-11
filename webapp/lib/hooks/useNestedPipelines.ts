// Starts BELOW the dataset `useConnector` already reads; each named dataset's read serves its whole nest.

import { datasetPipelineRead, failureKind, operatorMessage } from "@/lib/api";
import { reportIncident } from "@/lib/diagnostics";
import { heldRead, readThrough, type ReadDescriptor, type ReadResult } from "@/lib/read-cache";
import { useRead } from "./useRead";
import type {
  DatasetPipelineResponse,
  NestedPipelineRef,
  NodeConfigParam,
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
  schema: Record<string, NodeConfigParam[]> | null;
  // Null is UNKNOWN, never shut.
  reach: Record<string, NodeReach> | null;
  nestsNode: string | null;
  read: ReadResult<unknown>;
}

interface NestedPipelines {
  // Every layer after the one it nests in.
  layers: NestedLayer[];
  // The served reasons a nest was not read, joined; null where every one was.
  unread: string | null;
}

const EMPTY: NestedPipelines = { layers: [], unread: null };

// The `parent` of every layer the campaign's own pipeline nests.
export const CAMPAIGN_LAYER = "campaign";

function collect(
  dataset: string,
  resp: DatasetPipelineResponse,
  parent: string,
  depth: number,
  out: { layers: NestedLayer[]; unread: string[] },
): void {
  const key = `${parent}>${dataset}`;
  out.layers.push({
    key,
    parent,
    depth,
    dataset,
    connector: resp.connector,
    view: resp.view,
    schema: resp.node_config_schema,
    reach: resp.reach,
    nestsNode: resp.nests[0]?.node ?? null,
    read: heldRead(resp),
  });
  for (const n of resp.nests) {
    if (n.pipeline !== null) collect(n.dataset, n.pipeline, key, depth + 1, out);
    else if (n.unread !== null) out.unread.push(n.unread);
  }
}

function nestedPipelinesRead(roots: NestedPipelineRef[]): ReadDescriptor<NestedPipelines> {
  return {
    id: `nested-pipelines\x1f${roots.map((r) => `${r.node}>${r.dataset}`).join("\x1e")}`,
    load: async (signal) => {
      const out = { layers: [] as NestedLayer[], unread: [] as string[] };
      for (const ref of roots) {
        let resp: DatasetPipelineResponse;
        try {
          resp = await readThrough(datasetPipelineRead(ref.dataset), signal);
        } catch (e) {
          if (signal.aborted) throw e;
          // The level stays in the stack, carrying its failure; what it nests is unknown.
          const kind = failureKind(e);
          reportIncident(e, { surface: "nested-pipelines", address: ref.dataset });
          out.layers.push({
            ...unread(ref),
            read: { status: "failed", failure: { kind, message: operatorMessage(e, kind) }, kept: null },
          });
          continue;
        }
        collect(ref.dataset, resp, CAMPAIGN_LAYER, 0, out);
      }
      const data = { layers: out.layers, unread: out.unread.length > 0 ? out.unread.join("; ") : null };
      return { kind: "ok", data, validator: null };
    },
  };
}

export function useNestedPipelines(roots: NestedPipelineRef[], enabled: boolean): NestedPipelines {
  const read = useRead(enabled && roots.length > 0 ? nestedPipelinesRead(roots) : null);

  if (!enabled || roots.length === 0) return EMPTY;
  if (read.status === "ready") return read.data;
  // Publish the named level now: zero layers would make the caller's level innermost for a frame.
  return { layers: roots.map(unread), unread: null };
}

function unread(ref: NestedPipelineRef): NestedLayer {
  return {
    key: `${CAMPAIGN_LAYER}>${ref.dataset}`,
    parent: CAMPAIGN_LAYER,
    depth: 0,
    dataset: ref.dataset,
    connector: null,
    view: null,
    schema: null,
    reach: null,
    nestsNode: null,
    read: { status: "loading", kept: null },
  };
}
