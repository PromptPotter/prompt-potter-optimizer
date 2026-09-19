"use client";
import type { RowStatus } from "@/lib/derivations";

// ONE campaign row, wherever a campaign is offered — the sidebar tree and the masthead
// switcher. Presentational: every value arrives already derived (`lib/derivations/
// campaign-summary.ts`), so the two surfaces cannot word one campaign two ways.

// The glyph stands in for a word the column cannot fit whole; the word is its accessible name.
export function PhaseMark({ status }: { status: RowStatus }) {
  return (
    <span
      className={`unit-library-mark tone-${status.mark.tone}`}
      role="img"
      aria-label={status.word}
    >
      {status.mark.glyph}
    </span>
  );
}

// Ten runs of one dataset share a display name, so the row keeps its id's `__suffix` whole and
// lets the name before it truncate — the tail is what tells the rows apart. The second line
// carries what the tail cannot say: what this run runs with, how far it got, when it last moved.
export function CampaignRowLabel({
  name,
  suffix,
  status,
  spend,
  parts,
}: {
  name: string;
  suffix: string | null;
  status: RowStatus | null;
  spend: string;
  parts: string[];
}) {
  const line = parts.join(" · ");
  return (
    <span className="unit-library-row">
      <span className="unit-library-name unit-library-name-split">
        <span className="unit-library-name-head">{name}</span>
        {suffix && <span className="unit-library-name-tail">__{suffix}</span>}
      </span>
      <span className="unit-library-meta unit-library-meta-marked">
        {status && <PhaseMark status={status} />}
        <span className="unit-library-spend">{spend}</span>
      </span>
      <span className="unit-library-sub" title={line}>
        {line}
      </span>
    </span>
  );
}
