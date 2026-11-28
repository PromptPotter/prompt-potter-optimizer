// The one door to the wire's shapes: every generated type and served word table, and the narrow
// aliases derived from them. Nothing imports `types.generated` but this file.
export * from "./types.generated";

import type {
  ActivityResponse,
  ReadQueries,
  CampaignPipelineResponse,
  CellsResponse,
  CommandPayloads,
  CycleEntryFacts,
  EditDraftPatch,
  HierarchyCampaign,
  HierarchyCandidate,
  HierarchyCycle,
  HierarchyResponse,
  HierarchyRound,
  ReadPair,
  ServedDashboard,
  SubtreeCampaign,
  SubtreeCycle,
  UnreadPair,
} from "./types.generated";

// served: `domain/hierarchy.py::ListedCampaign` — a started campaign, or an open check-in
// (`entry_kind: "unstarted"`). Narrow on `entry_kind` once.
export type ListedCampaign = HierarchyResponse["entries"][number];

// What the forest and a bar draw: a run, or one candidate of one. Narrow on `entry_kind` once.
export type LineageNode = SubtreeCycle | HierarchyCandidate;

// Any entry the listing serves, and (`RowEntry`) any a row or a card renders: a `SubtreeCycle`
// extends its listing one, a `SubtreeCampaign` shares only its base (none of a listed row's acts).
// A round is a node of the tree, never a row.
export type HierarchyEntry = HierarchyCampaign | HierarchyCycle | HierarchyCandidate;
export type RowEntry = HierarchyEntry | SubtreeCampaign;
// served: `domain/hierarchy.py::HierarchyEntry` — what `GET /hierarchy/{address}` answers.
export type SubtreeEntry = SubtreeCampaign | SubtreeCycle | HierarchyRound | HierarchyCandidate;
export type CycleMint = CycleEntryFacts["mint"];
// served: `domain/paired_reading.py::PairedReading` — narrow on `state === "read"` once.
export type PairedReading = ReadPair | UnreadPair;

// A partial config, as sent: every key omittable at any depth.
export type DraftPatch = Partial<Omit<EditDraftPatch, "config">> &
  Pick<CommandPayloads["edit-draft-campaign"]["patch"], "config">;
// served: `domain/cells.py::HeatmapScope`
export type HardSamplesScope = CellsResponse["scope"];
export type HardSampleOrder = CellsResponse["order"];
export type CellStatus = CellsResponse["cells"][number]["status"];

export type MeasuredUnit = ServedDashboard["measured_unit"];
// served: `command_kinds.py::filable` — a kind the read's own reader may file.
export type Filable = SubtreeCampaign["facts"]["filable"][number];

export type ActivityWindow = ActivityResponse["window"];
export type ActivityGroupBy = NonNullable<ReadQueries["activity"]["group_by"]>;

// provider → model → capability; the provider key is the node's own `provider` value, "" for none.
export type CapabilityMenu = CampaignPipelineResponse["model_capabilities"];
