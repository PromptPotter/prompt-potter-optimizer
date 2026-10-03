"use client";
import { useMemo } from "react";
import { useRoundRows } from "@/lib/hooks/useRoundRows";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import type { SelectedCandidate } from "@/lib/types";
import {
  candidateObserveConfig,
  liveCandidateObserveConfig,
  searchpointCopyChoices,
} from "@/lib/derivations";
import { CopyButton, Toolbar, ToolbarSpacer } from "@/components/ui";
import { useConnector } from "@/lib/hooks/useConnector";
import { SearchpointDrillIn } from "@/components/shell/searchpoint/SearchpointDrillIn";
import { SteerForkAction } from "@/components/shell/searchpoint/SteerForkAction";
import { VerifyAction } from "@/components/shell/searchpoint/VerifyAction";
import { MeasurementsPane } from "@/components/shell/measurements/MeasurementsPane";

interface Props {
  selected: SelectedCandidate | null;
  onClose: () => void;
}

// Dashboard HOST for `shell/searchpoint/SearchpointDrillIn`: it owns only the streaming cycle, the
// round in flight and the fork verb. No stitch (`useRoundRows`): the live round never fetches its file.
export function ScoringInspector({ selected, onClose }: Props) {
  const { dash } = useDashboard();
  const cv = useConnector();
  const { viewedPath } = useWorkspace();
  const round = useRoundRows(selected?.round ?? null);
  const arms = round.rows.length;
  // On LABEL: an in-flight row has no lineage id until it is scored.
  const row = selected ? round.row(selected.label) : null;
  const samples = useMemo(() => round.samples(row), [round, row]);

  const cfg = !selected
    ? null
    : round.live
      ? liveCandidateObserveConfig(dash, selected.label)
      : candidateObserveConfig(round.doc, selected.label, selected.label);

  if (!selected) return null;

  return (
    <section className="scoring-inspector" aria-label="Scoring inspector">
      <Toolbar className="inspector-head">
        <span className="inspector-title">Scoring · {selected.label}</span>
        <ToolbarSpacer />
        {/* The payload comes from the shared builder, never from this host. */}
        <CopyButton
          choices={searchpointCopyChoices({ cfg, row, samples, arms: arms || null })}
          title={`Copy ${selected.label}`}
        />
        <button
          type="button"
          className="inspector-close"
          onClick={onClose}
          aria-label="Close inspector"
          title="Close"
        >
          ×
        </button>
      </Toolbar>
      <SearchpointDrillIn
        row={row}
        cfg={cfg}
        measurements={
          <MeasurementsPane
            preset={{ candidateId: selected.candidate_id, scope: "cycle", groupBy: "none" }}
          />
        }
        arms={arms || null}
        schema={cv.nodeConfigSchema}
        schemaStatus={cv.pipelineStatus}
        outputSchema={cv.nodeOutputSchema}
        pending={
          round.live
            ? `Scoring in progress for R${selected.round} — the spec and its numbers appear as this candidate's samples land.`
            : `Round file not yet on disk for R${selected.round}.`
        }
        actions={
          // The VIEWED address, so an L4 inner searchpoint is refused rather than forked at the
          // outer cycle.
          <>
            <VerifyAction candidate={selected} path={viewedPath} />
            <SteerForkAction
              candidate={selected}
              path={viewedPath}
              dash={dash}
              parentIsLive={cv.isLive}
              schema={cv.nodeConfigSchema}
              schemaStatus={cv.pipelineStatus}
              isSingleNode={cv.isSingleNode}
              outputSchema={cv.nodeOutputSchema}
            />
          </>
        }
      />
    </section>
  );
}
