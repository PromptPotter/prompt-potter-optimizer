"use client";
import { useMemo, useState } from "react";
import { useRoundFile } from "@/lib/hooks/useRoundFile";
import { useConnector } from "@/lib/hooks/useConnector";
import { fmtPct1 } from "@/lib/format";
import type { SelectedCandidate } from "@/lib/types/selection";
import type { ScoreboardEntry } from "@/lib/types/round";
import type { DashboardSnapshot } from "@/lib/poll";
import { Dialog } from "@/components/ui/Dialog";
import { SteerForkPanel } from "@/components/dashboard/control-panel/SteerForkPanel";

interface Props {
  campaignId: string | null;
  cycleId: string | null;
  selected: SelectedCandidate | null;
  dash: DashboardSnapshot | null;
  onClose: () => void;
}

export function ScoringInspector({
  campaignId,
  cycleId,
  selected,
  dash,
  onClose,
}: Props) {
  // Lazy-fetch the selected candidate's round file. The summary surface
  // (dash.rounds[]) carries accuracy + is_winner per candidate but not the
  // composite/hits/per_sample[] block — those are deep-audit fields, so the
  // inspector reaches for the round file only when the operator opens it.
  const { doc } = useRoundFile(campaignId, cycleId, selected?.round ?? null);
  const cv = useConnector();
  const [steerOpen, setSteerOpen] = useState(false);

  const entry = useMemo<ScoreboardEntry | null>(() => {
    if (!selected || !doc) return null;
    const scoreboard = doc.scoreboard as ScoreboardEntry[] | undefined;
    return scoreboard?.find((c) => (c.candidate_id ?? "") === selected.candidate_id) ?? null;
  }, [doc, selected]);

  if (!selected) return null;
  const data = entry;

  return (
    <section className="scoring-inspector" aria-label="Scoring inspector">
      <div className="inspector-head">
        <span>Scoring · R{selected.round}.{selected.candidate_id}</span>
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
      <div className="inspector-body">
        <div className="inspector-row">
          <span className="inspector-key">label</span>
          <span className="inspector-val">{selected.label}</span>
        </div>
        <div className="inspector-row">
          <span className="inspector-key">accuracy</span>
          <span className="inspector-val">{fmtPct1(selected.accuracy)}</span>
        </div>
        {data && typeof data.composite === "number" && (
          <div className="inspector-row">
            <span className="inspector-key">composite</span>
            <span className="inspector-val">{data.composite.toFixed(4)}</span>
          </div>
        )}
        {data && typeof data.hits === "number" && typeof data.total === "number" && (
          <div className="inspector-row">
            <span className="inspector-key">hits</span>
            <span className="inspector-val">{data.hits} / {data.total}</span>
          </div>
        )}
        <div className="inspector-row">
          <span className="inspector-key">winner</span>
          <span className="inspector-val">{selected.is_winner ? "yes" : "no"}</span>
        </div>
        {data == null && (
          <div className="inspector-note">
            Round file not yet on disk for R{selected.round} — showing only the
            in-tree summary.
          </div>
        )}
      </div>
      <div className="inspector-actions">
        <button
          type="button"
          className="fork-button"
          onClick={() => setSteerOpen(true)}
          title="Open this searchpoint in the control panel — review or edit its evolved prompt, node config, and run limits, then fork-continue optimizing from it. Edits are optional."
        >
          Steer &amp; fork
        </button>
      </div>
      {/* Steering is its own act with its own home — a modal control panel that
          opens straight from the inspector (no tab hop). The fork continues
          from this searchpoint (always `operator_steered`); edits are optional. */}
      {steerOpen && (
        <Dialog
          open
          title={`Steer & fork · R${selected.round}.${selected.candidate_id}`}
          onClose={() => setSteerOpen(false)}
        >
          <SteerForkPanel
            campaignId={campaignId}
            cycleId={cycleId}
            candidate={selected}
            dash={dash}
            isLive={cv.isLive}
            onDone={() => setSteerOpen(false)}
            onCancel={() => setSteerOpen(false)}
          />
        </Dialog>
      )}
    </section>
  );
}
