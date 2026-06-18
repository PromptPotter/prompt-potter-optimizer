import { displayFitness } from "@/lib/derivations";
import { type BarSlot } from "./FitnessChart";

// 1-based rank of each line by value descending; lines with a null value
// are dropped (unranked).
function ranks(lines: { key: string; v: number | null }[]): Map<string, number> {
  const sortable = lines.filter((l) => l.v != null).slice().sort((a, b) => (b.v as number) - (a.v as number));
  const m = new Map<string, number>();
  sortable.forEach((l, i) => m.set(l.key, i + 1));
  return m;
}

function pickWinner(lines: { key: string; v: number | null }[]): string | null {
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
// actual rank against its what-if rank and flags whether the winner flips.
// Spans every bar including origin and historical rounds.
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
    actual: displayFitness(b.composite, b.accuracy),
    whatif: b.whatif,
  }));
  const rankActual = ranks(lines.map((l) => ({ key: l.key, v: l.actual })));
  const rankWhatif = ranks(lines.map((l) => ({ key: l.key, v: l.whatif })));
  const wA = pickWinner(lines.map((l) => ({ key: l.key, v: l.actual })));
  const wW = pickWinner(lines.map((l) => ({ key: l.key, v: l.whatif })));
  const winnerLabel = (k: string | null) =>
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
  const winnerSwap = wA != null && wW != null && wA !== wW;
  const fmt = (v: number | null) => (v == null ? "—" : v.toFixed(3));
  return (
    <>
      <div>
        {winnerSwap
          ? <span className="rank-up">winner flips {winnerLabel(wA)} → {winnerLabel(wW)}</span>
          : <span className="rank-flat">winner unchanged ({winnerLabel(wA)})</span>}
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
