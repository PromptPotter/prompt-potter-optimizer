"use client";
import { useMemo, useState } from "react";
import { postCreateFork, postStopCycle } from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { useRoundFile } from "@/lib/useRoundFile";
import { Modal, type ModalAction } from "@/components/shell/Modal";
import { fmtPct1 } from "@/lib/format";
import type { SelectedCandidate } from "./SelectionContext";

interface Props {
  campaignId: string | null;
  cycleId: string | null;
  selected: SelectedCandidate | null;
  isLive: boolean;
  onClose: () => void;
}

interface ScoreboardEntry {
  candidate_id?: string;
  label?: string;
  accuracy?: number;
  composite?: number;
  hits?: number;
  total?: number;
  is_winner?: boolean;
  per_sample?: Record<string, unknown>[];
  [k: string]: unknown;
}

export function ScoringInspector({
  campaignId,
  cycleId,
  selected,
  isLive,
  onClose,
}: Props) {
  // Lazy-fetch the selected candidate's round file. The summary surface
  // (dash.rounds[]) carries accuracy + is_winner per candidate but not the
  // composite/hits/per_sample[] block — those are deep-audit fields, so the
  // inspector reaches for the round file only when the operator opens it.
  const { doc } = useRoundFile(campaignId, cycleId, selected?.round ?? null);
  const [forkPending, setForkPending] = useState(false);
  const [forkResult, setForkResult] = useState<{ id: string; cli: string } | null>(null);
  const [forkErr, setForkErr] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const entry = useMemo<ScoreboardEntry | null>(() => {
    if (!selected || !doc) return null;
    const scoreboard = doc.scoreboard as ScoreboardEntry[] | undefined;
    return scoreboard?.find((c) => (c.candidate_id ?? "") === selected.candidate_id) ?? null;
  }, [doc, selected]);

  if (!selected) return null;
  const data = entry;

  const runFork = async (alsoStop: boolean) => {
    if (!campaignId || !cycleId) return;
    setConfirming(false);
    setForkPending(true);
    setForkErr(null);
    try {
      if (alsoStop) {
        // Mid-run "Stop & fork" composes the two routes — stop the parent
        // first so the operator's next `resume` picks up the fork
        // cleanly instead of racing the still-running loop.
        await postStopCycle(campaignId, cycleId);
      }
      const r = await postCreateFork(
        campaignId,
        cycleId,
        selected.round,
        selected.candidate_id,
      );
      setForkResult({ id: r.fork_cycle_id, cli: r.cli_command });
      // Force the workspace poll to re-tick now — the new fork (and the
      // stop, if any) show up at once instead of a poll-interval later.
      bumpRevalidation();
    } catch (e) {
      setForkErr((e as Error).message);
    } finally {
      setForkPending(false);
    }
  };

  // Three-way modal when the parent is live; single-button when frozen.
  // "Stop & fork" leads as the recommended affordance — that's the common
  // operator intent when redirecting a dead-end cycle.
  const forkActions: ModalAction[] = isLive
    ? [
        { label: "Cancel", onClick: () => setConfirming(false) },
        { label: "Fork only", onClick: () => void runFork(false) },
        { label: "Stop & fork", variant: "primary", onClick: () => void runFork(true) },
      ]
    : [
        { label: "Cancel", onClick: () => setConfirming(false) },
        { label: "Fork", variant: "primary", onClick: () => void runFork(false) },
      ];

  const forkMessage = isLive
    ? `${cycleId} is currently running. Forking from R${selected.round}.${selected.candidate_id} mints a sibling rooted at this candidate. "Stop & fork" halts the parent first (recommended) so your next \`resume\` picks up the fork without racing the live loop. "Fork only" leaves the parent running.`
    : `Endorses R${selected.round}.${selected.candidate_id}. The new fork inherits the parent's ledger and runs L1 generation fresh from this point. The active pointer retargets to the fork — your next \`resume\` picks it up.`;

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
      {!forkResult && (
        <div className="inspector-actions">
          <button
            type="button"
            className="fork-button"
            onClick={() => setConfirming(true)}
            disabled={forkPending}
            title="Endorse this candidate and mint a fork rooted here. L1 then generates fresh candidates from this point."
          >
            {forkPending ? "Forking…" : "Endorse & fork"}
          </button>
          <button
            type="button"
            className="fork-button-secondary"
            disabled
            title="Operator-supplied candidates — write your own list of searchpoints that replace the next L1 generate output. Needs a typed form; landing in M12."
          >
            Substitute candidates
            <span className="fork-coming-soon">M12</span>
          </button>
          {forkErr && <span className="fork-err">fork: {forkErr}</span>}
        </div>
      )}
      <Modal
        open={confirming}
        title={isLive ? "Fork from a running unit?" : "Fork from this point?"}
        message={forkMessage}
        actions={forkActions}
        onClose={() => setConfirming(false)}
      />
      {forkResult && (
        <div className="inspector-fork-result">
          <div>
            Forked → <code>{forkResult.id}</code>
          </div>
          <div className="fork-cli">
            <span>Run:</span>
            <code>{forkResult.cli}</code>
          </div>
          <div className="inspector-note">
            Active pointer retargeted to the new fork.
          </div>
        </div>
      )}
    </section>
  );
}
