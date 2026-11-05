// The one door to the wire's shapes: every generated type and served word table, and the narrow
// aliases derived from them. Nothing imports `types.generated` but this file.
export * from "./types.generated";

import type {
  ActivityResponse,
  CampaignPipelineResponse,
  CellsResponse,
  CourseFacts,
  EditDraftPatch,
  HierarchyCampaign,
  HierarchyCandidate,
  HierarchyCourse,
  HierarchyResponse,
  HierarchyRound,
  ServedDashboard,
  SubtreeCampaign,
  SubtreeCourse,
} from "./types.generated";

// served: `domain/hierarchy.py::ListedCampaign` — a started campaign, or an open check-in
// (`level: "unstarted"`). Narrow on `level` once.
export type ListedCampaign = HierarchyResponse["entries"][number];

// What the forest and a bar draw: a run, or one candidate of one. Narrow on `level` once.
export type LineageNode = SubtreeCourse | HierarchyCandidate;

// Any entry a row or a card renders, the listing's or a subtree's: each `Subtree*` type extends its
// listing one. A round is a node of the tree, never a row.
export type HierarchyEntry = HierarchyCampaign | HierarchyCourse | HierarchyCandidate;
// served: `domain/hierarchy.py::HierarchyEntry` — what `GET /hierarchy/{address}` answers.
export type SubtreeEntry = SubtreeCampaign | SubtreeCourse | HierarchyRound | HierarchyCandidate;
export type CourseKind = CourseFacts["mint"];

export type DraftPatch = Partial<EditDraftPatch>;
// served: `domain/cells.py::HeatmapScope`
export type HardSamplesScope = CellsResponse["scope"];
export type HardSampleOrder = CellsResponse["order"];
export type CellStatus = CellsResponse["cells"][number]["status"];

export type MeasuredUnit = ServedDashboard["measured_unit"];

export type ActivityWindow = ActivityResponse["window"];
export type ActivityGroupBy = ActivityResponse["group_by"];

// provider → model → capability; the provider key is the node's own `provider` value, "" for none.
export type CapabilityMenu = CampaignPipelineResponse["model_capabilities"];
