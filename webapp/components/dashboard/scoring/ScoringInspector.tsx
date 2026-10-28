"use client";
import { useRound } from "@/lib/hooks/useRound";
import { usePresetCells, type CellsPreset } from "@/lib/hard-samples";
import { useCycleStream } from "@/lib/poll";
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
import { CompareOriginAction } from "@/components/shell/searchpoint/CompareOriginAction";
import { MeasurementsPane } from "@/components/shell/measurements/MeasurementsPane";

interface Props {
  selected: SelectedCandidate | null;
  onClose: () => void;
}

export function ScoringInspector({ selected, onClose }: Props) {
  if (!selected) return null;
  return <Inspector selected={selected} onClose={onClose} />;
}

function Inspector({ selected, onClose }: { selected: SelectedCandidate; onClose: () => void }) {
  const { dash } = useCycleStream();
  const cv = useConnector();
  const { viewedPath } = useWorkspace();
  const round = useRound(viewedPath, selected.round);
  const row = round.row(selected.arm);
  const preset: CellsPreset = { candidateId: selected.arm, scope: "cycle" };
  const { cells } = usePresetCells(preset);

  const cfg = round.unfiled
    ? liveCandidateObserveConfig(dash, selected.arm)
    : candidateObserveConfig(round.doc, selected.arm, selected.label);
  const benchPass =
    dash?.verification.find((m) => m.kind === "bench" && m.arm.key === selected.arm) ?? null;

  return (
    <section className="scoring-inspector" aria-label="Scoring inspector">
      <Toolbar className="inspector-head">
        <span className="inspector-title">Scoring · {selected.label}</span>
        <ToolbarSpacer />
        <CopyButton
          choices={searchpointCopyChoices({ cfg, reading: row?.reading ?? null, cells })}
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
        reading={row?.reading ?? null}
        cfg={cfg}
        benchPass={benchPass?.state === "done" ? null : benchPass}
        measurements={<MeasurementsPane preset={{ ...preset, groupBy: "none" }} />}
        schema={cv.schema}
        pending={
          round.unfiled
            ? `Scoring in progress for R${selected.round} — the spec and its numbers appear as this candidate's samples land.`
            : `Round file not yet on disk for R${selected.round}.`
        }
        actions={
          // The VIEWED address: an L4 inner searchpoint is refused, never forked at the outer cycle.
          <>
            <CompareOriginAction candidate={selected} path={viewedPath} />
            <VerifyAction candidate={selected} path={viewedPath} />
            <SteerForkAction
              candidate={selected}
              path={viewedPath}
              schema={cv.schema}
            />
          </>
        }
      />
    </section>
  );
}
