"use client";
import { isSelectedCandidate, type CandidateRow, type SampleRow, type SelectedCandidate } from "@/lib/types";
import type { LineageNode } from "@/lib/api";
import { SampleRowItem } from "./SampleRowItem";
import { PanelCellRow } from "./PanelCellRow";
import { fmtPct0 } from "@/lib/format";
import { panelCellKey } from "@/lib/derivations";
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
  // The cycle these groups were READ from (the viewed leaf). A row carries no cycle
  // of its own, and a `candidate_id` is unique only within one — so this is what says
  // whether `selectedCandidate` names a row here or a row in some other tree.
  cycleId: string | null;
  // Names the candidate row clicked, or null to clear. This renderer doesn't build
  // the selection itself — that needs the cycle the round was read from, which the
  // view owns.
  onSelectCandidate: (c: CandidateRow | null) => void;
  // PANEL MODE. Non-null when this course is L4, i.e. its samples are inner
  // campaigns rather than scored rows: `(candidate_label, cell)` → the run that
  // measured that cell. Null for an ordinary course, which renders scored rows.
  // Null vs empty is a real distinction — empty means L4 with the sandbox not yet
  // read, and the cells still list.
  panel: ReadonlyMap<string, LineageNode> | null;
  onOpenRun: (run: LineageNode) => void;
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
  cycleId,
  onSelectCandidate,
  panel,
  onOpenRun,
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
        {/* Hidden in panel mode: an L4 cell has no HIT/MISS to filter on. */}
        {!panel && (
          <SegmentedControl
            options={STATUS_FILTERS}
            value={statusFilter}
            onChange={onStatusFilter}
            ariaLabel="HIT/MISS filter"
          />
        )}
        <span className="rsv-count">
          {totalRows} {panel ? "cells" : "samples"}
        </span>
      </div>
      <div className="rsv-groups">
        {groups.map((g) => {
          const isCandSelected = isSelectedCandidate(
            selectedCandidate,
            cycleId,
            g.candidate.round,
            g.candidate.candidate_id,
          );
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
                onClick={() => onSelectCandidate(isCandSelected ? null : g.candidate)}
                title="Click to anchor lineage + fitness on this candidate"
              >
                <span className="rsv-cand-label">{g.candidate.label}</span>
                <span className="rsv-tally">
                  {/* An L4 cell carries no `is_hit` — it was optimized by a whole
                      campaign, not scored — so a HIT/MISS tally here counted every
                      null as a miss and read "HIT 0 / MISS 7" beside a 39%
                      headline. The panel's own count is the honest reading. */}
                  {panel ? (
                    <span className="tag-cached" title="Each cell is an inner campaign">
                      {g.samples.length} {g.samples.length === 1 ? "cell" : "cells"}
                    </span>
                  ) : (
                    <>
                      <span className="tag-hit">HIT {hits}</span>
                      <span className="tag-miss">MISS {misses}</span>
                    </>
                  )}
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
                  {display.map((s) =>
                    panel ? (
                      <PanelCellRow
                        key={s.key}
                        cell={s.query}
                        run={panel.get(panelCellKey(g.candidate.label, s.query)) ?? null}
                        cached={s.cached}
                        onOpen={onOpenRun}
                      />
                    ) : (
                      <SampleRowItem key={s.key} row={s} />
                    ),
                  )}
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
