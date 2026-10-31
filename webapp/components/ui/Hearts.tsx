import { memo } from "react";
import type { LivesReading } from "@/lib/api/types";
import { LIVES_WORDS } from "@/lib/api/types.generated";
import { livesLabel, livesPips, livesSpent } from "@/lib/derivations";
import { HeartIcon } from "./HeartIcon";

// An empty bank is a skull, never an empty row, which reads as "no lives mode".
export const Hearts = memo(function Hearts({
  lives,
  className,
}: {
  lives: LivesReading;
  className?: string;
}) {
  const label = livesLabel(lives);
  const { banked, spent } = livesPips(lives);
  return (
    <span className={className ?? "hearts"} title={label} aria-label={label} role="img">
      {livesSpent(lives) ? (
        <span className="hearts-dead" aria-hidden="true">
          {LIVES_WORDS.empty}
        </span>
      ) : (
        Array.from({ length: banked + spent }, (_, i) => <HeartIcon key={i} filled={i < banked} />)
      )}
    </span>
  );
});
