import type { ArmNode, LensShift } from "@/lib/api";
import type { CandidateBar } from "@/lib/types";
import { fmtNum } from "@/lib/format";

const MOVE_MARK: Record<NonNullable<ArmNode["lens_rank_move"]>, { glyph: string; cls: string }> = {
  up: { glyph: "▲", cls: "rank-up" },
  down: { glyph: "▼", cls: "rank-down" },
  unchanged: { glyph: "·", cls: "rank-flat" },
};

export function FitnessRankSummary({
  views,
  shift,
  criterion,
}: {
  views: CandidateBar[];
  // served: `CourseNode.lens_shift` — the top by composite, not the θ-elected round winner.
  shift: LensShift | null;
  // `lensOf(mask) != null`, not "are tiles ticked": Expression mode builds a criterion with no tiles.
  criterion: boolean;
}) {
  if (views.length === 0) {
    return (
      <span className="empty">
        Evaluator registry loads with round 1, then candidates surface here as scoring completes — toggle these on/off to preview alternative scoring without re-running.
      </span>
    );
  }
  if (!criterion) {
    return (
      <span className="empty">
        No criterion yet — tick a tile above, or type one in Expression mode.
      </span>
    );
  }
  return (
    <>
      {shift && (
        <>
          <div>
            {shift.top_changed
              ? <span className="rank-up">top fitness flips {shift.top_composite ?? "—"} → {shift.top_lens ?? "—"}</span>
              : <span className="rank-flat">top fitness unchanged ({shift.top_composite ?? "—"})</span>}
          </div>
          <div>
            <span className="rank-up">▲ {shift.moved_up}</span> moved up · <span className="rank-down">▼ {shift.moved_down}</span> moved down · <span className="rank-flat">· {shift.unchanged}</span> unchanged
          </div>
        </>
      )}
      <div style={{ marginTop: 6 }}>
        candidates: {views.map((b, i) => {
          const move = b.arm?.lens_rank_move;
          const mark = move ? MOVE_MARK[move] : { glyph: "—", cls: "rank-flat" };
          return (
            <span key={b.key}>
              {i > 0 && " · "}
              {b.label} {fmtNum(b.reading?.own?.composite?.value ?? null, 3)}→{fmtNum(b.arm?.lens_value ?? null, 3)}{" "}
              <span className={mark.cls}>{mark.glyph}</span>
            </span>
          );
        })}
      </div>
    </>
  );
}
