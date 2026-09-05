"use client";
import { useMemo } from "react";
import { useRoundSource } from "@/lib/hooks/useRoundSource";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import type { ElectedRow, SelectedCandidate } from "@/lib/types";
import {
  candidateObserveConfig,
  liveCandidateObserveConfig,
  samplesForRow,
} from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import { useRoundCandidates } from "@/lib/hooks/useRoundCandidates";
import { SearchpointDrillIn } from "@/components/shell/searchpoint/SearchpointDrillIn";
import { SteerForkAction } from "@/components/shell/searchpoint/SteerForkAction";

interface Props {
  selected: SelectedCandidate | null;
  onClose: () => void;
}

// The dashboard's HOST for the searchpoint drill-in — the reading itself is
// `shell/searchpoint/SearchpointDrillIn`, because a Compare channel shows the same point the same
// way and two copies would drift. What lives here is what only the dashboard can answer: which
// cycle is streaming, which round is in flight, and the fork verb that steers it.
//
// Source pick follows the no-stitch rule (`useRoundSource`): a completed round's spec comes from
// its round file, the in-flight round's from `dashboard.json`'s live l1_score input, and the live
// round's not-yet-written file is never fetched.
export function ScoringInspector({ selected, onClose }: Props) {
  const { dash } = useDashboard();
  const cv = useConnector();
  // Round files follow the VIEWED leaf hop — so an L4 inner loop's candidate
  // resolves its `round_NNNN.json` from the inner cycle's dir, not the outer
  // root's empty `rounds/` (the old "round file not on disk" degradation).
  const { viewedPath } = useWorkspace();
  const { isLive, doc } = useRoundSource(viewedPath, selected?.round ?? null, dash);

  // The candidate's own row, and the arms it stood against — one lookup, one source. Composite
  // and accuracy used to be read a SECOND time off `doc.scoreboard` beside it; both are
  // projections of one `ScoredCandidate`, so that was one fact fetched twice and free to disagree.
  const { byRound } = useRoundCandidates();
  const arms = selected ? (byRound.get(selected.round) ?? []).length : 0;
  const row = useMemo<ElectedRow | null>(() => {
    if (!selected) return null;
    // On LABEL: a selection is minted off the served tree and carries the lineage id, while an
    // in-flight row has none until it is scored — so an id match resolved every CLOSED round and
    // no live one, which is the whole round the operator is watching.
    return (byRound.get(selected.round) ?? []).find((c) => c.label === selected.label) ?? null;
  }, [selected, byRound]);
  const samples = useMemo(() => (row ? samplesForRow(row, dash, doc) : []), [row, dash, doc]);

  // The selected candidate's runnable spec, through the one observe join every spec surface
  // reads. Round 0 = origin, and it resolves here like any other point.
  const cfg = !selected
    ? null
    : isLive
      ? liveCandidateObserveConfig(dash, selected.label)
      : candidateObserveConfig(doc, selected.label, selected.label);

  if (!selected) return null;

  return (
    <section className="scoring-inspector" aria-label="Scoring inspector">
      <div className="inspector-head">
        <span>Scoring · {selected.label}</span>
        <button
          type="button"
          className="inspector-close"
          onClick={onClose}
          aria-label="Close inspector"
          title="Close"
        >
          ×
        </button>
      </div>
      <SearchpointDrillIn
        row={row}
        cfg={cfg}
        samples={samples}
        arms={arms || null}
        schema={cv.nodeConfigSchema}
        outputSchema={cv.nodeOutputSchema}
        pending={
          isLive
            ? `Scoring in progress for R${selected.round} — the spec and its numbers appear as this candidate's samples land.`
            : `Round file not yet on disk for R${selected.round}.`
        }
        actions={
          // The VIEWED address, so an L4 inner searchpoint is refused rather than forked at the
          // outer cycle by a coincidental id — the guard is the action's, and it used to exist
          // only on the Compare side of the same click.
          <SteerForkAction
            candidate={selected}
            path={viewedPath}
            dash={dash}
            parentIsLive={cv.isLive}
            schema={cv.nodeConfigSchema}
            outputSchema={cv.nodeOutputSchema}
          />
        }
      />
    </section>
  );
}
