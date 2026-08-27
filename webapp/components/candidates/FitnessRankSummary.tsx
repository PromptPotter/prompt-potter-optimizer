import type { CandidateView } from "@/lib/types";
import { fmtNum } from "@/lib/format";

// Both rank maps are SERVED (`composite_rank` / `lens_rank`, ranked among siblings by the
// backend) — this panel folds them, it does not build them. An ordering is a score, and the
// local sort that used to live here re-answered the question under its own tie rule.
function ranks(lines: { key: string; r: number | null }[]): Map<string, number> {
  return new Map(lines.filter((l) => l.r != null).map((l) => [l.key, l.r as number]));
}

// Top bar by composite value — NOT the campaign winner (the real crown is
// θ-elected `isWinner`). This mask panel ranks on composite; no θ-election claim.
function topByFitness(rank: Map<string, number>): string | null {
  for (const [key, r] of rank) if (r === 1) return key;
  return null;
}

// Rank-shift read-out for the scoring mask — compares each candidate's
// actual composite rank against its masked rank and flags whether the top
// fitness bar flips. Spans every bar including origin and historical rounds.
export function FitnessRankSummary({
  views,
  criterion,
}: {
  views: CandidateView[];
  // Whether the editor above has produced a criterion at all — `lensOf(mask) != null`. Asked as
  // the one question rather than "are tiles ticked", because the expression mode builds a
  // criterion with no tiles at all and read the empty-selection way.
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
  const lines = views.map((b) => ({
    key: b.key,
    label: b.label,
    actual: b.composite ?? null,
    masked: b.lensValue,
    actualRank: b.compositeRank,
    maskedRank: b.lensRank,
  }));
  const rankActual = ranks(lines.map((l) => ({ key: l.key, r: l.actualRank })));
  const rankMasked = ranks(lines.map((l) => ({ key: l.key, r: l.maskedRank })));
  const wA = topByFitness(rankActual);
  const wW = topByFitness(rankMasked);
  const topLabel = (k: string | null) =>
    k == null ? "—" : (lines.find((l) => l.key === k)?.label ?? "—");
  let movedUp = 0, movedDown = 0, flat = 0;
  for (const l of lines) {
    const rA = rankActual.get(l.key);
    const rW = rankMasked.get(l.key);
    if (rA == null || rW == null) continue;
    if (rA > rW) movedUp += 1;
    else if (rA < rW) movedDown += 1;
    else flat += 1;
  }
  const topSwap = wA != null && wW != null && wA !== wW;
  return (
    <>
      <div>
        {topSwap
          ? <span className="rank-up">top fitness flips {topLabel(wA)} → {topLabel(wW)}</span>
          : <span className="rank-flat">top fitness unchanged ({topLabel(wA)})</span>}
      </div>
      <div>
        <span className="rank-up">▲ {movedUp}</span> moved up · <span className="rank-down">▼ {movedDown}</span> moved down · <span className="rank-flat">· {flat}</span> unchanged
      </div>
      <div style={{ marginTop: 6 }}>
        candidates: {lines.map((l, i) => {
          const rA = rankActual.get(l.key);
          const rW = rankMasked.get(l.key);
          const arrow = rA != null && rW != null
            ? (rA > rW ? <span className="rank-up">▲</span> : rA < rW ? <span className="rank-down">▼</span> : <span className="rank-flat">·</span>)
            : <span className="rank-flat">—</span>;
          return (
            <span key={l.key}>
              {i > 0 && " · "}
              {l.label} {fmtNum(l.actual, 3)}→{fmtNum(l.masked, 3)} {arrow}
            </span>
          );
        })}
      </div>
    </>
  );
}
