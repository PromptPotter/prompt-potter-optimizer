import { memo } from "react";
import { HeartIcon } from "./HeartIcon";
import { heartPips, heartsLabel } from "@/lib/derivations";

// The one ♥ bank for a run's remaining lives. Hollow pips are the denominator; zero renders a skull,
// never an empty row, which would read as "no lives mode".
export const Hearts = memo(function Hearts({
  hearts,
  cap,
  className,
}: {
  hearts: number;
  cap: number | null | undefined;
  className?: string;
}) {
  const { filled, empty, dead } = heartPips(hearts, cap);
  const label = heartsLabel(hearts, cap);
  return (
    <span className={className ?? "hearts"} title={label} aria-label={label} role="img">
      {dead ? (
        <span className="hearts-dead" aria-hidden="true">
          💀
        </span>
      ) : (
        <>
          {Array.from({ length: filled }, (_, i) => (
            <HeartIcon key={`f${i}`} filled />
          ))}
          {Array.from({ length: empty }, (_, i) => (
            <HeartIcon key={`e${i}`} filled={false} />
          ))}
        </>
      )}
    </span>
  );
});
