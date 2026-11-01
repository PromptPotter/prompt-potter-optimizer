import { memo } from "react";
import { LIVES_WORDS, type Said } from "@/lib/api/types.generated";
import { HeartIcon } from "./HeartIcon";

// The served `lives` row: its value's pips drawn as hearts, an empty bank its skull, never an empty
// row, which reads as "no lives mode".
export const Hearts = memo(function Hearts({ row, className }: { row: Said; className?: string }) {
  const label = row.sub ?? row.label;
  return (
    <span className={className ?? "hearts"} title={label} aria-label={label} role="img">
      {row.value === LIVES_WORDS.empty ? (
        <span className="hearts-dead" aria-hidden="true">
          {LIVES_WORDS.empty}
        </span>
      ) : (
        Array.from(row.value ?? "", (pip, i) => (
          <HeartIcon key={i} filled={pip === LIVES_WORDS.banked} />
        ))
      )}
    </span>
  );
});
