import { Chip, IconTree } from "@/components/ui";

// The one button between the tree's two layouts: paired under the bars, or freed as its own card.
// Its own module, so the eager ForestCard never pulls the lazy CandidatesCard (and chart.js) in.
export function TreePairing({
  freed,
  setFreed,
  descendants,
}: {
  freed: boolean;
  setFreed: (freed: boolean) => void;
  descendants: number;
}) {
  return (
    <Chip
      icon={descendants === 0}
      on={freed}
      ariaLabel={
        freed
          ? "Pair the lineage tree under the bars"
          : `Free the lineage tree from the bars — the full campaign tree, ${descendants} descendant${descendants === 1 ? "" : "s"}`
      }
      title={freed ? "Pair the tree under the bars" : "Free the tree from the bars"}
      onClick={() => setFreed(!freed)}
    >
      <span className="cand-forest-toggle">
        <IconTree />
        {descendants > 0 && <span className="cand-view-count">{descendants}</span>}
      </span>
    </Chip>
  );
}
