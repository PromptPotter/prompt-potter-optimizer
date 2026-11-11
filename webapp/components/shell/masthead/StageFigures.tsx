import { Figure, FigureRow } from "@/components/shell/FigureRow";
import { Loading, Said } from "@/components/ui";
import { figureOf } from "@/lib/derivations";
import { useViewedCourse } from "@/lib/lineage";

// The stage view, read across a room: the served climb leads (the selection's lift vs origin and vs
// parent, then what it cost), its shared caveat beneath; the lift figure with its absolute rates
// follows, worded by its reading state until it is read.
export function StageFigures() {
  const course = useViewedCourse();
  if (course === null) return <Loading className="stage">Reading this run…</Loading>;
  const lift = figureOf(course, "lift");
  const climb = course.climb;
  return (
    <div className="stage">
      {climb !== null && (
        <div className="stage-climb">
          <Said tone={climb.tone} label={climb.label} detail={climb.detail} />
        </div>
      )}
      {lift !== null && (
        <FigureRow>
          <Figure row={lift} />
        </FigureRow>
      )}
    </div>
  );
}
