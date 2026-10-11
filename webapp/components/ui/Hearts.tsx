import { memo } from "react";
import type { LivesReading } from "@/lib/api/types";
import { HeartIcon } from "./HeartIcon";

// An empty bank is a skull, never an empty row, which reads as "no lives mode".
export const Hearts = memo(function Hearts({
  lives,
  className,
}: {
  lives: LivesReading;
  className?: string;
}) {
  return (
    <span className={className ?? "hearts"} title={lives.label} aria-label={lives.label} role="img">
      {lives.spent ? (
        <span className="hearts-dead" aria-hidden="true">
          {lives.bar}
        </span>
      ) : (
        Array.from(lives.bar, (pip, i) => <HeartIcon key={i} filled={pip === "♥"} />)
      )}
    </span>
  );
});
