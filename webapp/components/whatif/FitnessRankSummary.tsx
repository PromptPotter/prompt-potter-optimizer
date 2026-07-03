import { type BarSlot } from "./FitnessChart";

// 1-based rank of each line by value descending; lines with a null value
// are dropped (unranked).
function ranks(lines: { key: string; v: number | null }[]): Map<string, number> {
  const sortable = lines.filter((l) => l.v != null).slice().sort((a, b) => (b.v as number) - (a.v as number));
  const m = new Map<string, number>();
  sortable.forEach((l, i) => m.set(l.key, i + 1));
  return m;
}

// Top bar by composite value — NOT the campaign winner (the real crown is
// θ-elected `isWinner`). This what-if panel ranks on composite; no θ-election claim.
function topByFitness(lines: { key: string; v: number | null }[]): string | null {
  let best: string | null = null;
  let bestVal = -Infinity;
  for (const l of lines) {
    if (l.v == null) continue;
    if (l.v > bestVal) {
      bestVal = l.v;
      best = l.key;
    }
  }
  return best;
}

// Rank-shift read-out for the what-if ablation — compares each candidate's
// actual composite rank against its what-if rank and flags whether the top
// fitness bar flips. Spans every bar including origin and historical rounds.
export function FitnessRankSummary({
  bars,
  selected,
}: {
  bars: BarSlot[];
  selected: Set<string>;
}) {
  if (bars.length === 0) {
    return (
      <span className="empty">
        Evaluator registry loads with round 1, then candidates surface here as scoring completes — toggle these on/off to preview alternative scoring without re-running.
      </span>
    );
  }
  if (selected.size === 0) {
    return (
      <span className="empty">
        No evaluators selected — pick one or more tiles above to recompute scores.
      </span>
    );
  }
  const lines = bars.map((b) => ({
    key: b.key,
    label: b.label,
    // Server-resolved fitness; `?? accuracy` only covers in-flight bars whose
    // composite hasn't been served yet.
    actual: b.composite ?? b.accuracy,
    whatif: b.whatif,
  }));
  const rankActual = ranks(lines.map((l) => ({ key: l.key, v: l.actual })));
  const rankWhatif = ranks(lines.map((l) => ({ key: l.key, v: l.whatif })));
  const wA = topByFitness(lines.map((l) => ({ key: l.key, v: l.actual })));
  const wW = topByFitness(lines.map((l) => ({ key: l.key, v: l.whatif })));
  const topLabel = (k: string | null) =>
    k == null ? "—" : (lines.find((l) => l.key === k)?.label ?? "—");
  let movedUp = 0, movedDown = 0, flat = 0;
  for (const l of lines) {
    const rA = rankActual.get(l.key);
    const rW = rankWhatif.get(l.key);
    if (rA == null || rW == null) continue;
    if (rA > rW) movedUp += 1;
    else if (rA < rW) movedDown += 1;
    else flat += 1;
  }
  const topSwap = wA != null && wW != null && wA !== wW;
  const fmt = (v: number | null) => (v == null ? "—" : v.toFixed(3));
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
          const rW = rankWhatif.get(l.key);
          const arrow = rA != null && rW != null
            ? (rA > rW ? <span className="rank-up">▲</span> : rA < rW ? <span className="rank-down">▼</span> : <span className="rank-flat">·</span>)
            : <span className="rank-flat">—</span>;
          return (
            <span key={l.key}>
              {i > 0 && " · "}
              {l.label} {fmt(l.actual)}→{fmt(l.whatif)} {arrow}
            </span>
          );
        })}
      </div>
    </>
  );
}
