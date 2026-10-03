"use client";
import { VendorLogo } from "@/components/ui";
import { cx } from "@/lib/cx";
import type { RowStatus } from "@/lib/derivations";

// ONE campaign row for the sidebar tree and the masthead switcher; values arrive derived
// (`lib/derivations/campaign-summary.ts`).

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

// The SCAN surface, never a config dump: no campaign id here — the hover card is the audit one.
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
