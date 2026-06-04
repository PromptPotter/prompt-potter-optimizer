"use client";
import { useMemo, useState } from "react";
import {
  roundOf,
  type DashboardSnapshot,
  type StatusKind,
} from "@/lib/poll";
import { CardFrame } from "@/components/ui/Card";
import { useSelection } from "@/lib/SelectionContext";
import { useRoundSource } from "@/lib/hooks/useRoundSource";
import { roundCandidatesByRound } from "@/lib/derivations/round-candidates";
import {
  historicalSamplesFor,
  liveSamplesFor,
} from "@/lib/derivations/round-samples";
import type { SampleRow } from "@/lib/types/sample";
import type { CandidateRow } from "@/lib/types/candidate";

// Single per-round samples surface. Live mode reads from
// `dashboard.json` only; historical mode reads `round_NNNN.json`
// only — the two paths never merge (AGENTS.md no-stitch rule).
// The candidate list comes from `roundCandidatesByRound` in both
// modes so the displayed groups stay aligned with lineage + fitness.

interface Props {
  dash: DashboardSnapshot | null;
  status: StatusKind;
  campaignId: string | null;
  cycleId: string | null;
}

type StatusFilter = "all" | "hit" | "miss";

// Cap on rendered rows per candidate group so the DOM stays bounded
// even on long rounds. Operator can expand the candidate to see all
// rows the source has — the cap only affects DOM rendering count.
const PER_GROUP_CAP = 250;

export function RoundSamplesView({ dash, status, campaignId, cycleId }: Props) {
  const { round: selectedRound, setSelectionForCandidate, setSelectionForRound, candidate } =
    useSelection();
  const liveRound = roundOf(dash);
  // null = follow live: fall through to the in-flight round when one
  // exists. A completed-round pick stays explicit until the operator
  // clicks the live pill or another tab. `useRoundSource` owns the
  // live/historical guard + the historical round-file fetch.
  const effectiveRound = selectedRound ?? liveRound;
  const {
    isLive: isLiveView,
    doc: roundDoc,
    loading: roundLoading,
    error: roundError,
  } = useRoundSource(campaignId, cycleId, effectiveRound, dash);

  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [candFilter, setCandFilter] = useState<string>("all");

  // Candidate list for this round — single source of truth shared with
  // LineageTree and FitnessPanel. Excludes origin since C0 has no
  // per-sample stream of its own (origin's samples come from the
  // dataset's archive, not from a round file).
  const byRound = useMemo(() => roundCandidatesByRound(dash), [dash]);
  const candidates: CandidateRow[] = useMemo(() => {
    if (effectiveRound == null) return [];
    return (byRound.get(effectiveRound) ?? []).filter((c) => !c.is_origin);
  }, [byRound, effectiveRound]);

  // Build per-candidate samples lists. Live mode pulls directly from
  // the in-flight projection (compact string lines parsed once via
  // `parseSampleLine`); historical mode pulls from the round file's
  // `all_candidate_results`. Both readers return the same `SampleRow`
  // shape so the renderer below stays source-agnostic.
  const groups = useMemo(() => {
    if (effectiveRound == null) return [];
    const out: { candidate: CandidateRow; samples: SampleRow[] }[] = [];
    for (const c of candidates) {
      const raw = isLiveView
        ? liveSamplesFor(dash, effectiveRound, c.candidate_id)
        : historicalSamplesFor(roundDoc, effectiveRound, c.candidate_id);
      const filtered = raw.filter((s) => {
        if (statusFilter === "all") return true;
        if (statusFilter === "hit") return s.status === "HIT";
        return s.status === "MISS";
      });
      out.push({ candidate: c, samples: filtered });
    }
    if (candFilter !== "all") {
      return out.filter((g) => g.candidate.candidate_id === candFilter);
    }
    return out;
  }, [candidates, candFilter, statusFilter, isLiveView, dash, roundDoc, effectiveRound]);

  const totalRows = useMemo(
    () => groups.reduce((n, g) => n + g.samples.length, 0),
    [groups],
  );

  const title =
    effectiveRound == null
      ? "Samples"
      : isLiveView
        ? `Samples · R${effectiveRound}`
        : `Round ${effectiveRound} · samples`;

  const liveness = status === "live" ? "rolling" : "snapshot";
  const actions =
    effectiveRound == null ? null : isLiveView ? (
      <span className="badge">{liveness}</span>
    ) : (
      <button
        type="button"
        className="badge round-back-live"
        onClick={() => setSelectionForRound(null)}
        title="Follow the in-flight round again"
      >
        ← live
      </button>
    );

  return (
    <CardFrame
      className="round-samples-view"
      headingTag="h2"
      title={title}
      actions={actions}
    >
      {effectiveRound == null ? (
        <div className="samples-empty">
          No rounds yet. Samples appear here once the optimizer starts
          scoring — start it with{" "}
          <code>python -m promptpotter resume</code>.
        </div>
      ) : !isLiveView && roundLoading ? (
        <div className="samples-empty">Loading round {effectiveRound}…</div>
      ) : !isLiveView && roundError ? (
        <div className="samples-empty">
          Could not load round {effectiveRound}: {roundError}
        </div>
      ) : candidates.length === 0 ? (
        <EmptyState status={status} isLiveView={isLiveView} round={effectiveRound} />
      ) : (
        <>
          <div className="rsv-filters">
            <select
              value={candFilter}
              onChange={(e) => setCandFilter(e.target.value)}
              aria-label="Filter by candidate"
              className="rsv-cand-select"
            >
              <option value="all">All candidates ({candidates.length})</option>
              {candidates.map((c) => (
                <option key={c.candidate_id} value={c.candidate_id}>
                  {c.label}
                  {c.accuracy != null ? ` · ${(c.accuracy * 100).toFixed(0)}%` : ""}
                </option>
              ))}
            </select>
            <div role="group" className="rsv-toggle" aria-label="HIT/MISS filter">
              {(["all", "hit", "miss"] as StatusFilter[]).map((f) => (
                <button
                  key={f}
                  type="button"
                  className={statusFilter === f ? "on" : ""}
                  onClick={() => setStatusFilter(f)}
                >
                  {f.toUpperCase()}
                </button>
              ))}
            </div>
            <span className="rsv-count">{totalRows} samples</span>
          </div>
          <div className="rsv-groups">
            {groups.map((g) => {
              const isCandSelected =
                candidate != null &&
                candidate.round === g.candidate.round &&
                candidate.candidate_id === g.candidate.candidate_id;
              const hits = g.samples.reduce(
                (n, s) => n + (s.status === "HIT" ? 1 : 0),
                0,
              );
              const misses = g.samples.length - hits;
              const display = g.samples.slice(0, PER_GROUP_CAP);
              const truncated = g.samples.length - display.length;
              return (
                <section
                  key={g.candidate.key}
                  className={`rsv-group${isCandSelected ? " selected" : ""}`}
                >
                  <button
                    type="button"
                    className="rsv-group-head"
                    onClick={() =>
                      setSelectionForCandidate(
                        isCandSelected
                          ? null
                          : {
                              round: g.candidate.round,
                              candidate_id: g.candidate.candidate_id,
                              label: g.candidate.label,
                              accuracy: g.candidate.accuracy,
                              is_winner: g.candidate.is_winner,
                            },
                      )
                    }
                    title="Click to anchor lineage + fitness on this candidate"
                  >
                    <span className="rsv-cand-label">{g.candidate.label}</span>
                    <span className="rsv-tally">
                      <span className="tag-hit">HIT {hits}</span>
                      <span className="tag-miss">MISS {misses}</span>
                    </span>
                  </button>
                  {g.samples.length === 0 ? (
                    <div className="rsv-empty-row">No matching samples.</div>
                  ) : (
                    <div className="rsv-rows">
                      {display.map((s) => (
                        <SampleRowItem key={s.key} row={s} />
                      ))}
                      {truncated > 0 && (
                        <div className="rsv-empty-row">
                          +{truncated} more (rendering capped at {PER_GROUP_CAP}).
                        </div>
                      )}
                    </div>
                  )}
                </section>
              );
            })}
          </div>
        </>
      )}
    </CardFrame>
  );
}

function SampleRowItem({ row }: { row: SampleRow }) {
  if (row.status == null && row.raw_line) {
    return (
      <div className="rsv-row rsv-row-raw">
        <span className="body">{row.raw_line}</span>
      </div>
    );
  }
  const tag = row.status === "HIT" ? "tag-hit" : "tag-miss";
  const pred = row.predicted ? row.predicted : "∅";
  return (
    <details className="rsv-row">
      <summary>
        <span className={tag}>{row.status ?? "—"}</span>
        <span className="idx">
          #{String(row.sample_id ?? "").padStart(3, "0")}
        </span>
        {row.elapsed_s != null && (
          <span className="elapsed">{row.elapsed_s.toFixed(1)}s</span>
        )}
        {row.scorer && <span className="scorer">{row.scorer}</span>}
        <span className="body">
          gt:{truncate(row.ground_truth, 22)} · pred:{truncate(pred, 22)}
          {row.query ? ` · q:${truncate(row.query, 36)}` : ""}
        </span>
      </summary>
      <div className="rsv-detail">
        <div>
          <span className="kv-label">query</span>
          <span>{row.query || "—"}</span>
        </div>
        <div>
          <span className="kv-label">ground truth</span>
          <span>{row.ground_truth || "—"}</span>
        </div>
        <div>
          <span className="kv-label">predicted</span>
          <span>{pred}</span>
        </div>
      </div>
    </details>
  );
}

function truncate(s: string | undefined, n: number): string {
  if (!s) return "—";
  return s.length > n ? s.slice(0, n) + "…" : s;
}

function EmptyState({
  status,
  isLiveView,
  round,
}: {
  status: StatusKind;
  isLiveView: boolean;
  round: number;
}) {
  if (isLiveView && status === "live") {
    return (
      <div className="samples-empty">
        No candidates running yet this round. They&apos;ll appear here as
        the optimizer scores them.
      </div>
    );
  }
  return (
    <div className="samples-empty">
      Round {round} carries no candidates.
    </div>
  );
}
