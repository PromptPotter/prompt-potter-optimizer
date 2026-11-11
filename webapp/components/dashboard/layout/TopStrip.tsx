import { memo } from "react";
import { useViewedCourse } from "@/lib/lineage";
import { figureOf } from "@/lib/derivations";
import { Hearts } from "@/components/ui";

// A grid box of its own: with no hearts it renders nothing, and leaves no gap.
export const TopStrip = memo(function TopStrip() {
  const lives = figureOf(useViewedCourse(), "lives");
  if (!lives) return null;

  return (
    <div className="topstrip box-full">
      <Hearts row={lives} className="topstrip-hearts" />
    </div>
  );
});
