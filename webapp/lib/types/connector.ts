// Canonical connector-state shape. One join of three streams:
//
//   - operator-level registered backends (`GET /backends`)
//   - dataset-overlay pipeline (`GET /datasets/{name}/pipeline`)
//   - live per-LLM-node observations (`dashboard.json::current_round.nodes`)
//
// `BackendConnection` (promptpotter/domain/backend.py) is the mother
// object on the Python side; `BackendResponse` is its wire shape. The match
// from dataset's `backend_name` → registered backend by `name` happens
// in `lib/hooks/useConnector.ts` (one place, one comment), so no
// component is doing case-sensitive string lookups in its JSX.
//
// `view` and `currentNodes` cover the pipeline-graph display + live
// model surface; the connector popover reads `active` + `others` for
// the security chips + multi-backend switcher.
//
// `health` is the real connector reachability probe (`GET /backends/{id}/health`
// → BackendClient.check_status), polled on a slow cadence — the connector node's
// true up/down. `isLive` (live per-node observations from the run) is a separate
// signal: whether the optimizer is actively scoring through this connector.

import type {
  BackendHealthResponse,
  BackendResponse,
  NestedPipelineRef,
  NodeConfigParam,
  NodeOutputSchema,
} from "@/lib/api";
import type { NodeDataLike, PipelineView } from "@/components/workflow";

// How the `GET /datasets/{name}/pipeline` read went. A null `view` alone cannot
// say WHY — in flight, failed, and no-dataset-bound all read as null, and the
// hero rendered the same silent dash for all three (claiming `aria-busy` forever
// on a read that had already failed). Consumers must distinguish them: an
// operator staring at a dash cannot tell a loading pipeline from a broken one.
export type PipelineStatus = "unbound" | "loading" | "ok" | "error";

export interface ConnectorView {
  connector: string | null;
  backendType: string | null;
  view: PipelineView | null;
  // Why `view` is what it is. Pairs with `view`; never infer state from `view`
  // being null.
  pipelineStatus: PipelineStatus;
  active: BackendResponse | null;
  others: BackendResponse[];
  baseUrl: string | null;
  isTls: boolean | null;
  currentNodes: Record<string, NodeDataLike>;
  isLive: boolean;
  // The viewed cycle's fine-grained activity phase (`dashboard.json::state`):
  // "origin" | "scoring" | "l1_generate" | "l2_refining" | … The backend target
  // LLM is actually being called only during "scoring"/"origin"; the hero uses
  // this (with `isLive`) to show a node as running vs idle. Null when no run.
  phase: string | null;
  // Connector reachability from the slow `/backends/{id}/health` probe. Null
  // until the first probe lands (or when no backend is resolved).
  health: BackendHealthResponse | null;
  // The FULL operator-editable config surface per node (model/temperature/
  // thinking/max_tokens/provider) the steer + read-only node-detail panels
  // render, from `GET /datasets/{name}/pipeline`. Null until the dataset overlay
  // resolves (or in demo, where there's no real dataset).
  nodeConfigSchema: Record<string, NodeConfigParam[]> | null;
  // The per-node structured-output contract (read-only), shown beside the config
  // so the operator sees the whole node. Same fetch.
  nodeOutputSchema: Record<string, NodeOutputSchema | null> | null;
  // Which node of THIS pipeline runs another whole pipeline, and whose — served,
  // never guessed from a node name. Null for every ordinary dataset. It is what
  // lets the mechanics panel walk the layer stack past this dataset.
  nests: NestedPipelineRef | null;
}
