import { Figure, FigureRow } from "@/components/shell/FigureRow";
import { Loading, Said } from "@/components/ui";
import { figureOf } from "@/lib/derivations";
import { useViewedCourse } from "@/lib/lineage";

// The stage view, read across a room: the lift vs origin leads, worded by its reading state until it
// is read, and its served climb sits under it, their shared caveat once beneath.
export function StageFigures() {
  const course = useViewedCourse();
  if (course === null) return <Loading className="stage">Reading this run…</Loading>;
  const head = figureOf(course, "lift");
  const climb = course.climb;
  return (
    <div className="stage">
      {head !== null && (
        <FigureRow>
          <Figure row={head} />
        </FigureRow>
      )}
      {climb !== null && (
        <div className="stage-climb">
          <FigureRow>
            {climb.rates.map((row) => (
              <Figure key={row.key} row={row} />
            ))}
          </FigureRow>
          <Said tone={climb.note.tone} label={climb.note.label} detail={climb.note.detail} />
        </div>
      )}
    </div>
  );
}
