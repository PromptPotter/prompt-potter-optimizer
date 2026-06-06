"use client";
import type { ConnectorView } from "@/lib/types/connector";
import type { DashboardSnapshot } from "@/lib/poll";
import type { DraftCampaignWire } from "@/lib/api";
import { resolveSearchPoint } from "@/lib/derivations/searchPoint";
import { SearchPointPreview } from "./SearchPointPreview";

// Read-only searchpoint detail for a target/backend node, opened by clicking the
// node in TargetPipelineHero. It resolves WHICH searchpoint to show by context —
// a draft in origin setup, the live in-flight candidate, or the dataset origin
// (`resolveSearchPoint`) — then renders the one shared `SearchPointPreview`. The
// node-config schema / output schema ride the shared `useConnector()` view (no
// new fetch); during draft setup the prompt is the draft's, the config row is
// best-effort against the active view's schema (the ingest advanced expander is
// the authoritative draft config editor).
//
// Steering is a separate act with its own home — `ScoringInspector` opens the
// `SteerForkPanel` Dialog. This panel is inspect-only; it never forks.

interface Props {
  cv: ConnectorView;
  dash: DashboardSnapshot | null;
  // The active draft while a campaign is being set up; null otherwise. When set,
  // the preview shows the draft's searchpoint rather than the origin / live one.
  draft: DraftCampaignWire | null;
  onClose: () => void;
}

export function BackendNodeDetail({ cv, dash, draft, onClose }: Props) {
  const { point, label } = resolveSearchPoint({ draft, dash, cv });

  return (
    <SearchPointPreview
      point={point}
      schema={cv.nodeConfigSchema}
      outputSchema={cv.nodeOutputSchema}
      label={label}
      onClose={onClose}
    />
  );
}
