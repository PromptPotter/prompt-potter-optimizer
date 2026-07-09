import { memo } from "react";
import { HeartIcon } from "./HeartIcon";
import { heartPips, heartsLabel } from "@/lib/derivations";

// The ♥ bank, rendered once. Every surface that shows a run's remaining life mounts
// THIS — TopStrip, the lineage cycle node, the chat live segment — so "nearly dead"
// looks the same everywhere and no caller re-derives filled/empty from the cap.
//
// Hollow pips are not decoration: they are the denominator. A bare `♥♥♥` cannot tell
// healthy-of-four from nearly-dead-of-seven, and in lives mode `max_rounds` is null so
// the round counter carries no scale either. Zero hearts renders a skull, never an empty
// row — the absence of a mark reads as "no lives mode", which is a different fact.
//
// Accessibility: the icons are aria-hidden; the wrapper carries the words (BRAND.md —
// state pairs colour/shape with a label, never colour alone).
export const Hearts = memo(function Hearts({
  hearts,
  cap,
  size,
  className,
}: {
  hearts: number;
  cap: number | null | undefined;
  size?: number;
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
            <HeartIcon key={`f${i}`} filled size={size} />
          ))}
          {Array.from({ length: empty }, (_, i) => (
            <HeartIcon key={`e${i}`} filled={false} size={size} />
          ))}
        </>
      )}
    </span>
  );
});
