"use client";
import type { PairwiseComparison } from "@/lib/api/types";
import type { SampleRow } from "@/lib/types";
import { cx } from "@/lib/cx";
import { HoverCard } from "@/components/ui";

type Rows = Map<string, SampleRow>;

function byKey(rows: readonly SampleRow[]): Rows {
  return new Map(rows.map((r) => [r.sample_key, r]));
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
  const rows = { was: byKey(before), now: byKey(after) };
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
      {gained.length > 0 ? <FlipKeys label="now right" kind="gained" keys={gained} rows={rows} /> : null}
      {lost.length > 0 ? <FlipKeys label="now wrong" kind="lost" keys={lost} rows={rows} /> : null}
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

function FlipKeys({
  label,
  kind,
  keys,
  rows,
}: {
  label: string;
  kind: "gained" | "lost";
  keys: string[];
  rows: { was: Rows; now: Rows };
}) {
  return (
    <>
      <FlipSep />
      <HoverCard
        className="subject-flip-card"
        content={
          <ul className="subject-flip-list">
            {keys.map((key) => {
              const now = rows.now.get(key);
              const slot = now?.position ?? rows.was.get(key)?.position;
              return (
                <li key={key}>
                  <span className="subject-flip-id">
                    {slot === undefined ? "#—" : `#${String(slot).padStart(3, "0")}`}
                  </span>
                  <span className="subject-flip-was">{rows.was.get(key)?.predicted || "∅"}</span>
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
          <strong>{keys.length}</strong> {label}
        </span>
      </HoverCard>
    </>
  );
}
