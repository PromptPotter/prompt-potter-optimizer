import { Figure, FigureRow } from "@/components/shell/FigureRow";
import { Empty, Loading, ReadState, Said } from "@/components/ui";
import { figureOf, type FigureKey } from "@/lib/derivations";
import { useActivePointer } from "@/lib/hooks/useRegistry";
import { useViewedCourse } from "@/lib/lineage";
import { useViewedListing } from "@/lib/workspace";

// The live round's figures, then the lift, as the masthead's row serves them.
const SHOWN: readonly FigureKey[] = ["state", "rounds", "lift"];

// The stage, read across a room from a phone up to a TV: the followed run (a running one, else the
// latest launch, as the workspace follows it) under its served name, its served climb leading,
// then its served figures.
// It holds no control.
export function Stage() {
  const { read } = useActivePointer();
  return (
    <main className="stage-screen">
      <ReadState read={read} subject="the run to follow">
        {(active) =>
          active.campaign_id === null ? (
            <Empty title="No run to follow yet">
              This screen follows your running campaign, else your latest launch.
            </Empty>
          ) : (
            <StageRun />
          )
        }
      </ReadState>
    </main>
  );
}

function StageRun() {
  const listing = useViewedListing();
  const course = useViewedCourse();
  if (course === null) return <Loading>Reading this run…</Loading>;
  const figures = SHOWN.map((key) => figureOf(course, key)).filter((row) => row !== null);
  const climb = course.climb;
  return (
    <div className="stage">
      {listing !== null && <h1 className="stage-name">{listing.label}</h1>}
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
