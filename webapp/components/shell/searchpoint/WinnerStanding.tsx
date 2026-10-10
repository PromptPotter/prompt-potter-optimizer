"use client";
import type { RunStanding } from "@/lib/api/types";
import { fmtLift } from "@/lib/derivations";
import { HoverCard } from "@/components/ui";
import { PairedLift } from "@/components/shell/PairedLift";

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

export function WinnerStanding({ standing }: { standing: RunStanding }) {
  const { rounds_closed, improved, advanced } = standing;
  if (rounds_closed === 0) return null;
  return (
    <div className="subject-facts" aria-label="Where the run stands">
      <span>{plural(rounds_closed, "round")}</span>
      <HoverCard
        content={
          <p className="subject-fact-note">
            A round promotes an arm on a bare point estimate — it moves the search on and is not a
            result. A round separates when its pick&rsquo;s lift over the origin, on the cells both
            answered, excludes zero: that count is the one a result quotes.
          </p>
        }
      >
        <span tabIndex={0}>
          {improved} promoted, {advanced} separated
        </span>
      </HoverCard>
      <span>
        origin{" "}
        <PairedLift reading={standing.vs_origin}>
          {(lift, cells) => (
            <HoverCard
              content={
                <p className="subject-fact-note">
                  The origin and the selection on the {plural(cells, "origin-panel cell")} both
                  answered — the only set the two rates may be compared on. The pair above is the
                  selection against its parent on the selection&rsquo;s own rows.
                </p>
              }
            >
              <span tabIndex={0}>
                {fmtLift(lift, "rates")} on {cells} shared
              </span>
            </HoverCard>
          )}
        </PairedLift>
      </span>
    </div>
  );
}
