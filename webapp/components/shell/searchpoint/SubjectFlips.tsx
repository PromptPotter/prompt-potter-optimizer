"use client";
import type { PairwiseComparison } from "@/lib/api/types";
import type { SampleRow } from "@/lib/types";
import { cx } from "@/lib/cx";
import { HoverCard } from "@/components/ui";

type Rows = Map<number, SampleRow>;

function byId(rows: readonly SampleRow[]): Rows {
  const out: Rows = new Map();
  for (const r of rows) if (r.sample_id != null) out.set(r.sample_id, r);
  return out;
}

export function SubjectFlips({
  vsOrigin,
  partial,
  before,
  after,
}: {
  vsOrigin: PairwiseComparison | null;
  partial: { scored: number; expected: number | null } | null;
  before: readonly SampleRow[];
  after: readonly SampleRow[];
}) {
  const counts = vsOrigin?.hit.headline?.flips;
  const coverage = vsOrigin?.hit.coverage;
  if (!vsOrigin || !counts || !coverage) return null;
  const { gained, lost } = vsOrigin;
  const rows = { was: byId(before), now: byId(after) };
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
      {gained.length > 0 ? <FlipIds label="now right" kind="gained" ids={gained} rows={rows} /> : null}
      {lost.length > 0 ? <FlipIds label="now wrong" kind="lost" ids={lost} rows={rows} /> : null}
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

function FlipIds({
  label,
  kind,
  ids,
  rows,
}: {
  label: string;
  kind: "gained" | "lost";
  ids: number[];
  rows: { was: Rows; now: Rows };
}) {
  return (
    <>
      <FlipSep />
      <HoverCard
        className="subject-flip-card"
        content={
          <ul className="subject-flip-list">
            {ids.map((id) => {
              const now = rows.now.get(id);
              return (
                <li key={id}>
                  <span className="subject-flip-id">#{String(id).padStart(3, "0")}</span>
                  <span className="subject-flip-was">{rows.was.get(id)?.predicted || "∅"}</span>
                  <span aria-hidden="true">→</span>
                  <span className="subject-flip-now">{now?.predicted || "∅"}</span>
                  <span className="subject-flip-gt">want {now?.ground_truth_text ?? "—"}</span>
                </li>
              );
            })}
          </ul>
        }
      >
        <span className={cx("subject-flip-line", `is-${kind}`)} tabIndex={0}>
          <strong>{ids.length}</strong> {label}
        </span>
      </HoverCard>
    </>
  );
}
