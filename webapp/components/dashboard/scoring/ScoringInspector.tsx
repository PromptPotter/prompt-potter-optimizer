import { useRound } from "@/lib/hooks/useRound";
import { useCells, type CellsPreset } from "@/lib/hooks/useCells";
import { failureOf, shownData } from "@/lib/hooks/useRead";
import { useViewedDashboard } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import type { SelectedCandidate } from "@/lib/types";
import { COPY_WITHOUT_CELLS, searchpointCopyChoices } from "@/lib/derivations";
import { CopyButton, FailureLine, Toolbar, ToolbarSpacer } from "@/components/ui";
import { useConnector } from "@/lib/hooks/useConnector";
import { SearchpointDrillIn } from "@/components/shell/searchpoint/SearchpointDrillIn";
import { SteerForkAction } from "@/components/shell/searchpoint/SteerForkAction";
import { VerifyAction } from "@/components/shell/searchpoint/VerifyAction";
import { CompareOriginAction } from "@/components/shell/searchpoint/CompareOriginAction";
import { MeasurementsPane } from "@/components/measurements/MeasurementsPane";

interface Props {
  selected: SelectedCandidate | null;
  onClose: () => void;
}

export function ScoringInspector({ selected, onClose }: Props) {
  if (!selected) return null;
  return <Inspector selected={selected} onClose={onClose} />;
}

function Inspector({ selected, onClose }: { selected: SelectedCandidate; onClose: () => void }) {
  const { dash } = useViewedDashboard();
  const cv = useConnector();
  const { viewedPath } = useWorkspace();
  const round = useRound(viewedPath, selected.round);
  const reading = round.reading(selected.arm);
  const cfg = round.cfg(selected.arm, selected.label);
  const preset: CellsPreset = { candidateId: selected.arm, scope: "cycle" };
  const cellsRead = useCells(preset).read;
  const cells = shownData(cellsRead)?.cells ?? [];
  const benchPass =
    dash?.verification.find((m) => m.kind === "bench" && m.arm.key === selected.arm) ?? null;

  return (
    <section className="scoring-inspector" aria-label="Scoring inspector">
      <Toolbar className="inspector-head">
        <span className="inspector-title">Scoring · {selected.label}</span>
        <ToolbarSpacer />
        <CopyButton
          choices={searchpointCopyChoices({ cfg, reading, cells })}
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
      <FailureLine failure={failureOf(cellsRead)} lead={COPY_WITHOUT_CELLS} className="inspector-note" />
      <SearchpointDrillIn
        reading={reading}
        cfg={cfg}
        benchPass={benchPass?.state === "done" ? null : benchPass}
        measurements={<MeasurementsPane preset={{ ...preset, groupBy: "none" }} />}
        schema={cv.schema}
        failure={failureOf(round.read)}
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
