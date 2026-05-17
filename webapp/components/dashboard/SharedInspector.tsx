"use client";
import { ScoringInspector } from "./ScoringInspector";
import { useSelection } from "./SelectionContext";

interface Props {
  cycleId: string | null;
  isLive: boolean;
}

// Inspector slot for candidate selection (LineageTree + FitnessPanel clicks
// write to `selected`). Workflow node selection lives in `node` and is
// rendered by OptimizerNodeDetail in the Now lane, not here.
export function SharedInspector({ cycleId, isLive }: Props) {
  const { selected, setSelected } = useSelection();

  if (!selected) {
    return (
      <div className="shared-inspector empty">
        Select a lineage stub or a fitness bar above to inspect its searchpoint here.
      </div>
    );
  }

  return (
    <div className="shared-inspector">
      <ScoringInspector
        cycleId={cycleId}
        selected={selected}
        isLive={isLive}
        onClose={() => setSelected(null)}
      />
    </div>
  );
}
