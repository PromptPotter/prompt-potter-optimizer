// Canonical connector-state shape. One join of three streams:
//
//   - operator-level registered backends (`GET /backends`)
//   - dataset-overlay pipeline (`GET /datasets/{name}/pipeline`)
//   - live per-LLM-node observations (`dashboard.json::current_round.nodes`)
//
// `BackendConnection` (promptpotter/domain/backend.py) is the mother
// object on the Python side; `BackendInfo` is its wire shape. The match
// from dataset's `backend_name` → registered backend by `name` happens
// in `lib/hooks/useConnectorView.ts` (one place, one comment), so no
// component is doing case-sensitive string lookups in its JSX.
//
// `view` and `currentNodes` cover the pipeline-graph display + live
// model surface; the connector popover reads `active` + `others` for
// the security chips + multi-backend switcher.
//
// The real connector_state probe (live `/connector/{name}/health`) is
// an M12 control-plane add — `isLive` is its proxy today.

import type { BackendInfo, OptimizerLocks } from "@/lib/api";
import type { NodeDataLike, PipelineView } from "@/components/workflow/types";

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
  // The minted-pipeline optimizer-lock surface + origin prompt the backend-node
  // detail renders (from `GET /datasets/{name}/pipeline`). Null until the
  // dataset overlay resolves (or in demo, where there's no real dataset).
  optimizerLocks: OptimizerLocks | null;
  startingPrompt: Record<string, unknown> | null;
}
