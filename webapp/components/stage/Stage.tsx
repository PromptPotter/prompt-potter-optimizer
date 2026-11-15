import { Figure, FigureRow } from "@/components/shell/FigureRow";
import { Loading, ReadState, Said } from "@/components/ui";
import { figureOf, type FigureKey } from "@/lib/derivations";
import { useActivePointer } from "@/lib/hooks/useRegistry";
import { useViewedCycle, useViewedLineage } from "@/lib/state/lineage";

// The live round's figures, then the lift, as the masthead's row serves them.
const SHOWN: readonly FigureKey[] = ["state", "rounds", "lift"];

// The stage, read across a room from a phone up to a TV: the run the server says to follow
// (`cycle_addressing.py::followed_cycle`) under its served name, its served climb leading, then its
// served figures; the served line where it is not the account's own run, or where none is.
// It holds no control.
export function Stage() {
  const { read } = useActivePointer();
  return (
    <main className="stage-screen">
      <ReadState read={read} subject="the run to follow">
        {(active) => (
          <>
            {active.said !== null && (
              <div className="stage-said">
                <Said
                  tone={active.said.tone}
                  label={active.said.label}
                  detail={active.said.detail}
                />
              </div>
            )}
            {active.hop !== null && <StageRun />}
          </>
        )}
      </ReadState>
    </main>
  );
}

function StageRun() {
  const { tree } = useViewedLineage();
  const cycle = useViewedCycle();
  if (cycle === null) return <Loading>Reading this run…</Loading>;
  const figures = SHOWN.map((key) => figureOf(cycle, key)).filter(
    (row) => row !== null,
  );
  const climb = cycle.climb;
  return (
    <div className="stage">
      {tree !== null && <h1 className="stage-name">{tree.label}</h1>}
      {climb !== null && (
        <div className="stage-climb">
          <Said tone={climb.tone} label={climb.label} detail={climb.detail} />
        </div>
      )}
      {figures.length > 0 && (
        <FigureRow>
          {figures.map((row) => (
            <Figure key={row.key} row={row} />
          ))}
        </FigureRow>
      )}
    </div>
  );
}
