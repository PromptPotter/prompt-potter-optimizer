"use client";
import type { CandidateRow, SampleRow, SelectedCandidate } from "@/lib/types";
import { SampleRowItem } from "./SampleRowItem";
import { fmtPct0 } from "@/lib/format";
import { SegmentedControl, type Segment } from "@/components/ui";

export type StatusFilter = "all" | "hit" | "miss";

const STATUS_FILTERS: readonly Segment<StatusFilter>[] = [
  { value: "all", label: "ALL" },
  { value: "hit", label: "HIT" },
  { value: "miss", label: "MISS" },
];

// Cap on rendered rows per candidate group so the DOM stays bounded
// even on long rounds. Operator can expand the candidate to see all
// rows the source has — the cap only affects DOM rendering count.
const PER_GROUP_CAP = 250;

interface Props {
  candidates: CandidateRow[];
  groups: { candidate: CandidateRow; samples: SampleRow[] }[];
  totalRows: number;
  statusFilter: StatusFilter;
  onStatusFilter: (f: StatusFilter) => void;
  candFilter: string;
  onCandFilter: (id: string) => void;
  selectedCandidate: SelectedCandidate | null;
  onSelectCandidate: (c: SelectedCandidate | null) => void;
}

// Filters + per-candidate sample groups. Pure renderer extracted from
// RoundSamplesView, which owns the source (live vs round-file), the filter
// state, and the shared candidate selection. Both source readers return the
// same `SampleRow` shape so this stays source-agnostic.
export function RoundSamplesBody({
  candidates,
  groups,
  totalRows,
  statusFilter,
  onStatusFilter,
  candFilter,
  onCandFilter,
  selectedCandidate,
  onSelectCandidate,
}: Props) {
  return (
    <>
      <div className="rsv-filters">
        <select
          value={candFilter}
          onChange={(e) => onCandFilter(e.target.value)}
          aria-label="Filter by candidate"
          className="rsv-cand-select"
        >
          <option value="all">All candidates ({candidates.length})</option>
          {candidates.map((c) => (
            <option key={c.candidate_id} value={c.candidate_id}>
              {c.label}
              {c.accuracy != null ? ` · ${fmtPct0(c.accuracy)}` : ""}
            </option>
          ))}
        </select>
        <SegmentedControl
          options={STATUS_FILTERS}
          value={statusFilter}
          onChange={onStatusFilter}
          ariaLabel="HIT/MISS filter"
        />
        <span className="rsv-count">{totalRows} samples</span>
      </div>
      <div className="rsv-groups">
        {groups.map((g) => {
          const isCandSelected =
            selectedCandidate != null &&
            selectedCandidate.round === g.candidate.round &&
            selectedCandidate.candidate_id === g.candidate.candidate_id;
          const hits = g.samples.reduce(
            (n, s) => n + (s.status === "HIT" ? 1 : 0),
            0,
          );
          const misses = g.samples.length - hits;
          const cached = g.samples.reduce((n, s) => n + (s.cached ? 1 : 0), 0);
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
                  onSelectCandidate(
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
                  {cached > 0 && (
                    <span
                      className="tag-cached"
                      title="Samples reused from a prior identical searchpoint — no fresh backend call"
                    >
                      📖 {cached === g.samples.length ? "all cached" : `${cached} cached`}
                    </span>
                  )}
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
  );
}
