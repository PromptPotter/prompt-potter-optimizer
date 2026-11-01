"use client";
import type { ReactNode } from "react";
import { FAMILY_IDS, nodeOfKey, type FamilyId } from "@/lib/address";
import type { FigureKey } from "@/lib/derivations";
import { Empty } from "@/components/ui";
import { AccountView } from "@/components/account/AccountView";
import { NodeDetail } from "@/components/shell/node-surface/NodeDetail";
import { ComparePane } from "@/components/compare/ComparePane";
import { MeasurementsPane } from "@/components/measurements/MeasurementsPane";
import { CostStrip } from "@/components/dashboard/scoring/CostStrip";
import {
  CandidatesPane,
  HeatmapPane,
  LeaderPane,
  OptimizerPane,
  TimelinePane,
} from "@/components/dashboard/layout/panes";
import { ChatPane, FilesPane } from "./lazy-panes";

// The label is the view's command word (WCAG 2.5.3), so its pane's visible title contains it. A null
// pane is mounted by chrome: the campaign list is the sidebar, which a phone shows while it is the last view.
// `glance` keys the viewed course's figure the view reads at a glance.
type Family = { label: string; pane: ((key: string | null) => ReactNode) | null; glance?: FigureKey };

export const FAMILIES: Record<FamilyId, Family> = {
  chat: { label: "Chat", pane: () => <ChatPane />, glance: "state" },
  timeline: { label: "Timeline", pane: () => <TimelinePane />, glance: "rounds" },
  leader: { label: "Leader", pane: () => <LeaderPane />, glance: "selection" },
  candidates: { label: "Candidates", pane: () => <CandidatesPane />, glance: "candidates" },
  spend: { label: "Spend", pane: () => <CostStrip />, glance: "spend" },
  heatmap: { label: "Heatmap", pane: () => <HeatmapPane /> },
  samples: { label: "Samples", pane: (key) => <MeasurementsPane preset={{ groupBy: "sample" }} cell={key} /> },
  optimizer: { label: "Optimizer", pane: () => <OptimizerPane />, glance: "optimizer" },
  node: { label: "Node", pane: (key) => <NodePane nodeKey={key} /> },
  files: { label: "Files", pane: () => <FilesPane /> },
  compare: { label: "Compare", pane: () => <ComparePane /> },
  account: { label: "Account", pane: (key) => <AccountView section={key} /> },
  campaigns: { label: "Campaigns", pane: null },
};

function NodePane({ nodeKey }: { nodeKey: string | null }) {
  const node = nodeKey === null ? null : nodeOfKey(nodeKey);
  if (node === null) return <Empty className="box-full">Pick a node in a pipeline graph.</Empty>;
  return <NodeDetail node={node} />;
}

// The bottom nav: a phone shows each preset's LAST family first, so the dashboard opens on its
// leader with the candidates beside it.
export const PRESETS: readonly { label: string; glyph: ReactNode; families: readonly FamilyId[] }[] = [
  {
    label: "Chat",
    glyph: (
      <path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h9A1.5 1.5 0 0 1 14 4.5v5A1.5 1.5 0 0 1 12.5 11H6l-3 2.5V11H3.5A1.5 1.5 0 0 1 2 9.5z" />
    ),
    families: ["chat"],
  },
  {
    label: "Dashboard",
    glyph: <path d="M2.5 13V6.5M6.5 13V3M10.5 13V8M14 13H2" />,
    families: ["timeline", "spend", "heatmap", "optimizer", "candidates", "leader"],
  },
  {
    label: "Records",
    glyph: (
      <path d="M2.5 4.5A1 1 0 0 1 3.5 3.5h2.2l1.3 1.6h5.5a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1z" />
    ),
    families: ["samples", "files"],
  },
];

// Exact and case-insensitive; "<label> and <label>" names both. Anything else names no view.
export function familiesNamed(text: string): FamilyId[] | null {
  const named = text
    .trim()
    .toLowerCase()
    .split(/\s+and\s+/)
    .map((word) => FAMILY_IDS.find((f) => FAMILIES[f].label.toLowerCase() === word));
  return named.every((f): f is FamilyId => f !== undefined) ? named : null;
}
