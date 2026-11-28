import { Chip, IconTree } from "@/components/ui";

// The one button between the tree's two layouts: paired under the bars, or freed as its own card.
// Its own module, so the eager ForestCard never pulls the lazy CandidatesCard (and chart.js) in.
// `candidates` is the served count the tree draws; null until the subtree is read.
export function TreePairing({
  freed,
  setFreed,
  candidates,
}: {
  freed: boolean;
  setFreed: (freed: boolean) => void;
  candidates: number | null;
}) {
  const counted = candidates !== null && candidates > 0;
  return (
    <Chip
      icon={!counted}
      on={freed}
      ariaLabel={
        freed
          ? "Pair the lineage tree under the bars"
          : counted
            ? `Free the lineage tree from the bars — the full campaign tree, ${candidates} candidate${candidates === 1 ? "" : "s"}`
            : "Free the lineage tree from the bars — the full campaign tree"
      }
      title={freed ? "Pair the tree under the bars" : "Free the tree from the bars"}
      onClick={() => setFreed(!freed)}
    >
      <span className="cand-forest-toggle">
        <IconTree />
        {counted && <span className="cand-view-count">{candidates}</span>}
      </span>
    </Chip>
  );
}
