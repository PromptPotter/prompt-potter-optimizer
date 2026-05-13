"use client";
import { useMemo, useState } from "react";
import { postCreateFork } from "@/lib/api";
import { useRoundHistory } from "@/lib/use-round-history";
import type { SelectedCandidate } from "./LineageTree";

interface Props {
  cycleId: string | null;
  refreshKey: number;
  selected: SelectedCandidate | null;
  editMode: boolean;
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

interface RoundDoc {
  round?: number;
  scoreboard?: ScoreboardEntry[];
}

export function ScoringInspector({
  cycleId,
  refreshKey,
  selected,
  editMode,
  onClose,
}: Props) {
  const docs = useRoundHistory(cycleId, refreshKey) as RoundDoc[];
  const [forkPending, setForkPending] = useState(false);
  const [forkResult, setForkResult] = useState<{ id: string; cli: string } | null>(null);
  const [forkErr, setForkErr] = useState<string | null>(null);

  const entry = useMemo<ScoreboardEntry | null>(() => {
    if (!selected) return null;
    const r = docs.find((d) => d.round === selected.round);
    return r?.scoreboard?.find((c) => (c.candidate_id ?? "") === selected.candidate_id) ?? null;
  }, [docs, selected]);

  if (!selected) return null;
  const data = entry;

  const onFork = async () => {
    if (!cycleId) return;
    if (
      !window.confirm(
        `Fork from R${selected.round}.${selected.candidate_id}?\n\nEndorses this candidate. The new fork inherits the parent's ledger and runs L1 generation fresh from this point. The active pointer retargets to the new fork — your next \`optimize\` picks it up.`,
      )
    ) {
      return;
    }
    setForkPending(true);
    setForkErr(null);
    try {
      const r = await postCreateFork(cycleId, selected.round, selected.candidate_id);
      setForkResult({ id: r.fork_cycle_id, cli: r.cli_command });
    } catch (e) {
      setForkErr((e as Error).message);
    } finally {
      setForkPending(false);
    }
  };

  return (
    <aside className="card scoring-inspector" aria-label="Scoring inspector">
      <div className="card-title">
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
          <span className="inspector-val">{fmtPct(selected.accuracy)}</span>
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
      {editMode && !forkResult && (
        <div className="inspector-actions">
          <button
            type="button"
            className="fork-button"
            onClick={() => void onFork()}
            disabled={forkPending}
            title="Endorse this candidate and mint a fork rooted here"
          >
            {forkPending ? "Forking…" : "Fork from here"}
          </button>
          {forkErr && <span className="fork-err">fork: {forkErr}</span>}
        </div>
      )}
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
    </aside>
  );
}

function fmtPct(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : "—";
}
