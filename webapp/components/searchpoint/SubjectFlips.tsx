import type { PairwiseComparison, CellRow } from "@/lib/api/types";
import { useCells } from "@/lib/hooks/useCells";
import { failureOf, shownData } from "@/lib/hooks/useRead";
import { cx } from "@/lib/cx";
import { FailureLine, HoverCard } from "@/components/ui";
import { fmtSampleTag } from "@/lib/format";

export function SubjectFlips({
  vsOrigin,
  partial,
  before,
  after,
}: {
  vsOrigin: PairwiseComparison | null;
  partial: { scored: number; expected: number | null } | null;
  // The two arm keys whose cells the flips are read off, on the viewed cycle.
  before: string | null;
  after: string | null;
}) {
  const counts = vsOrigin?.hit.headline?.flips;
  const coverage = vsOrigin?.hit.coverage;
  if (!vsOrigin || !counts || !coverage) return null;
  const { gained, lost } = vsOrigin;
  const arms = { before, after };
  return (
    <div className="subject-flips">
      <span className="subject-flip-ref">vs origin</span>
      <FlipSep />
      <span className="subject-flip-total">
        {coverage.scored} both measured
        {partial
          ? ` — scoring, ${partial.scored}${partial.expected === null ? "" : ` of ${partial.expected}`} so far`
          : ""}
      </span>
      {gained.length > 0 ? <FlipKeys label="now right" kind="gained" keys={gained} arms={arms} /> : null}
      {lost.length > 0 ? <FlipKeys label="now wrong" kind="lost" keys={lost} arms={arms} /> : null}
      <FlipSep />
      <span className="subject-flip-same">
        {gained.length + lost.length === 0
          ? "no row changed hands — the lift is elsewhere"
          : `${counts.unchanged} unchanged`}
      </span>
    </div>
  );
}

// Carried by the segment that FOLLOWS it, so a segment rendering nothing takes its separator along.
function FlipSep() {
  return (
    <span className="subject-flip-sep" aria-hidden="true">
      ·
    </span>
  );
}

type FlipArms = { before: string | null; after: string | null };

function FlipKeys({
  label,
  kind,
  keys,
  arms,
}: {
  label: string;
  kind: "gained" | "lost";
  keys: string[];
  arms: FlipArms;
}) {
  return (
    <>
      <FlipSep />
      <HoverCard className="subject-flip-card" content={<FlipList keys={keys} arms={arms} />} focusable>
        <span className={cx("subject-flip-line", `is-${kind}`)}>
          <strong>{keys.length}</strong> {label}
        </span>
      </HoverCard>
    </>
  );
}

function bySlot(cells: readonly CellRow[]): Map<number, CellRow> {
  return new Map(cells.map((c) => [c.position, c]));
}

// Mounted only while the card is open, so the two reads cost nothing until a hover asks.
function FlipList({ keys, arms }: { keys: string[]; arms: FlipArms }) {
  const was = useCells(arms.before ? { candidateId: arms.before, scope: "cycle" } : null);
  const now = useCells(arms.after ? { candidateId: arms.after, scope: "cycle" } : null);
  const wasBody = shownData(was.read);
  const nowBody = shownData(now.read);
  const item = new Map(
    [...(wasBody?.samples ?? []), ...(nowBody?.samples ?? [])].map((it) => [it.sample_key, it]),
  );
  const wasAt = bySlot(wasBody?.cells ?? []);
  const nowAt = bySlot(nowBody?.cells ?? []);
  return (
    <>
      <FailureLine
        failure={failureOf(was.read) ?? failureOf(now.read)}
        lead="Could not read the flipped cells."
      />
      <ul className="subject-flip-list">
        {keys.map((key) => {
          const it = item.get(key);
          return (
            <li key={key}>
              <span className="subject-flip-id">
                {it === undefined ? "#—" : fmtSampleTag(it.position)}
              </span>
              <span className="subject-flip-was">{(it && wasAt.get(it.position)?.predicted) || "∅"}</span>
              <span aria-hidden="true">→</span>
              <span className="subject-flip-now">{(it && nowAt.get(it.position)?.predicted) || "∅"}</span>
              <span className="subject-flip-gt">want {it?.ground_truth ?? "—"}</span>
            </li>
          );
        })}
      </ul>
    </>
  );
}
