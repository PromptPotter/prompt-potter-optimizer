import type { RunStanding } from "@/lib/api/types";
import { HoverCard } from "@/components/ui";

// The rounds are the masthead's ROUNDS and the origin pair its BEST: this says only what the rounds did.
export function WinnerStanding({ standing }: { standing: RunStanding }) {
  const { rounds_closed, improved, advanced } = standing;
  if (rounds_closed === 0) return null;
  return (
    <div className="subject-facts" aria-label="Where the run stands">
      <HoverCard
        content={
          <p className="subject-fact-note">
            A round promotes an arm on a bare point estimate — it moves the search on and is not a
            result. A round separates when its pick&rsquo;s lift over the origin, on the cells both
            answered, excludes zero: that count is the one a result quotes.
          </p>
        }
        focusable
      >
        <span>
          {improved} promoted, {advanced} separated
        </span>
      </HoverCard>
    </div>
  );
}
