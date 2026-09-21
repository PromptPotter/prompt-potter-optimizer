"use client";
import { VendorLogo } from "@/components/ui";
import { cx } from "@/lib/cx";
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

// The row is a NAME and a reading, never a config dump. Two lines, and the split is by KIND:
// line one is what the campaign IS, line two is everything about it that is a value.
//
// Line one leads with the VENDOR marks, and they lead deliberately: fixed-width, at a fixed
// offset, so a stack of campaigns is countable by brand straight down the column — which is the
// whole reason the model moved out of the text. After them the name gets the rest of the line.
//
// The campaign's `__id` is NOT here at all — not beside the name, not on the second line. It is
// an id, it made the row read as an attribute rather than a thing, and at the resting sidebar
// width it cost so much room that `spreadsheetbench-s20` truncated to `spr…`. The hover card
// already carries it whole under "Campaign", which is the same call the routing levers get: the
// row is the SCAN surface, the card is the AUDIT one. What tells two runs of one dataset apart
// here is the vendor mark, the settings, the rounds and the spend.
//
// Line two is line-clamped, so `title` repeating it whole is the one job `title=` keeps
// (webapp/CLAUDE.md § Component conventions).
export function CampaignRowLabel({
  name,
  status,
  spend,
  parts,
  vendors,
}: {
  name: string;
  status: RowStatus | null;
  spend: string;
  parts: string[];
  vendors: readonly { vendor: string; models: string[] }[];
}) {
  const line = parts.join(" · ");
  return (
    <span className="unit-library-row">
      <span className="unit-library-name unit-library-name-split">
        {vendors.length > 0 && (
          <span className="unit-library-vendors">
            {vendors.map((v) => (
              <VendorLogo key={v.vendor} vendor={v.vendor} models={v.models} />
            ))}
          </span>
        )}
        <span className="unit-library-name-head">{name}</span>
      </span>
      <span className="unit-library-meta unit-library-meta-marked">
        {status && <PhaseMark status={status} />}
        <span className="unit-library-spend">{spend}</span>
      </span>
      {line && (
        <span
          className={cx("unit-library-sub", vendors.length > 0 && "unit-library-sub-indent")}
          title={line}
        >
          {line}
        </span>
      )}
    </span>
  );
}
