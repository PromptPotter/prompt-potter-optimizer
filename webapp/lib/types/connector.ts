// Canonical connector-state shape. One join of three streams:
//
//   - operator-level registered backends (`GET /backends`)
//   - dataset-overlay pipeline (`GET /datasets/{name}/pipeline`)
//   - live per-LLM-node observations (`dashboard.json::current_round.nodes`)
//
// `BackendConnection` (promptpotter/domain/backend.py) is the mother
// object on the Python side; `BackendInfo` is its wire shape. The match
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

import type { BackendHealth, BackendInfo, NodeConfigParam, NodeOutputSchema } from "@/lib/api";
import type { NodeDataLike, PipelineView } from "@/components/workflow";

export interface ConnectorView {
  connector: string | null;
  backendType: string | null;
  view: PipelineView | null;
  active: BackendInfo | null;
  others: BackendInfo[];
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
  health: BackendHealth | null;
  // The FULL operator-editable config surface per node (model/temperature/
  // thinking/max_tokens/provider) the steer + read-only node-detail panels
  // render, from `GET /datasets/{name}/pipeline`. Null until the dataset overlay
  // resolves (or in demo, where there's no real dataset).
  nodeConfigSchema: Record<string, NodeConfigParam[]> | null;
  // The per-node structured-output contract (read-only), shown beside the config
  // so the operator sees the whole node. Same fetch.
  nodeOutputSchema: Record<string, NodeOutputSchema | null> | null;
  originPromptFields: Record<string, unknown> | null;
}
