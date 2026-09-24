"use client";
import { useEffect, useId, useMemo, useState, type ReactNode } from "react";
import type {
  CellCandidate,
  CellRow,
  CellStatus,
  DatasetItem,
  HardSampleOrder,
  HardSamplesScope,
} from "@/lib/api";
import type { CellAddress } from "@/lib/address";
import type { CyclePath } from "@/lib/ids";
import { fmtPct0 } from "@/lib/format";
import { cx } from "@/lib/cx";
import { fitnessStyle } from "@/lib/derivations";
import { useHardSamples } from "@/lib/hard-samples";
import { useCells } from "@/lib/hooks/useCells";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import {
  DataTable,
  ErrorNote,
  SegmentedControl,
  Toolbar,
  ToolbarSpacer,
  type Column,
  type RowGroup,
} from "@/components/ui";
import { CellPanel } from "@/components/shell/cell/CellPanel";

// THE measurement log: every list of measured cells is a PRESET of this pane. Grouping buckets
// served rows under a served key order and never re-sorts them.

type GroupBy = "sample" | "candidate" | "none";

interface MeasurementsPreset {
  // IDENTITY, never source (I9).
  path?: CyclePath;
  datasetName?: string;
  // Any of these set makes the pane read its own slice rather than the shared roster.
  candidateId?: string;
  round?: number;
  scope?: HardSamplesScope;
  groupBy?: GroupBy;
}

const GROUPS = [
  { value: "sample", label: "Sample" },
  { value: "candidate", label: "Candidate" },
  { value: "none", label: "None" },
] as const;

type StatusPick = CellStatus | "all";
const STATUSES = [
  { value: "all", label: "All" },
  { value: "HIT", label: "Hit" },
  { value: "MISS", label: "Miss" },
  { value: "ERR", label: "Error" },
] as const;

const cellId = (c: CellRow) => `${c.candidate}\u001f${c.sample_id}`;

function Fitness({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="ms-dim">—</span>;
  return (
    <span className="ms-fit" style={fitnessStyle(value)}>
      {fmtPct0(value)}
    </span>
  );
}

function SampleHeader({ it, n }: { it: DatasetItem; n: number }) {
  return (
    <span className="ms-group-line">
      <span className="ms-rank">#{it.hard_sample_rank}</span>
      <span className="ms-sid">s{it.sample_id}</span>
      <span className="ms-query" title={it.query}>
        {it.query}
      </span>
      <span className="ms-dim">
        {it.n_measured}/{n} graded
      </span>
      <Fitness value={it.mean_fitness} />
      <span className="ms-dim" title="Fitted difficulty δ (higher = harder)">
        {it.delta == null ? "δ —" : `δ ${it.delta.toFixed(2)}`}
      </span>
    </span>
  );
}

function CandidateHeader({ c, n }: { c: CellCandidate; n: number }) {
  return (
    <span className="ms-group-line">
      <span className="ms-rank">{c.label}</span>
      {c.round != null && <span className="ms-dim">round {c.round}</span>}
      {c.live && <span className="ms-live">live</span>}
      <span className="ms-dim">{n} cells</span>
    </span>
  );
}

export function MeasurementsPane({
  preset = {},
  // Only Records → Measurements answers the ADDRESS's open cell; an embedded preset opens the
  // panel only for a row clicked on it.
  claimsAddress = false,
  heading,
}: {
  preset?: MeasurementsPreset;
  claimsAddress?: boolean;
  heading?: ReactNode;
}) {
  const shared = useHardSamples();
  const { viewedPath, openCell, openCellOwner, setOpenCell, releaseCell } = useWorkspace();
  const { isLive } = useDashboard();
  const paneId = useId();
  const [groupBy, setGroupBy] = useState<GroupBy>(preset.groupBy ?? "none");
  const [status, setStatus] = useState<StatusPick>("all");
  const [hideUnmeasured, setHideUnmeasured] = useState(false);
  const owns = openCellOwner === paneId || (openCellOwner === null && claimsAddress);
  useEffect(() => () => releaseCell(paneId), [releaseCell, paneId]);

  const scope = preset.scope ?? shared.scope;
  const datasetName = preset.datasetName ?? shared.datasetName;
  // Unfiltered on the viewed unit IS the shared roster — never a second fetch of it.
  const ownRead =
    preset.path !== undefined ||
    preset.datasetName !== undefined ||
    preset.candidateId !== undefined ||
    preset.round !== undefined ||
    status !== "all" ||
    scope !== shared.scope;
  const own = useCells(
    ownRead ? (preset.path ?? viewedPath) : null,
    datasetName,
    scope,
    shared.rankedByPick,
    // The dashboard's liveness is the VIEWED unit's; a preset on another one does not poll.
    preset.path === undefined && isLive,
    {
      candidateId: preset.candidateId,
      round: preset.round,
      status: status === "all" ? undefined : status,
    },
  );
  const data = ownRead ? own : shared;
  const error = data.error;
  const stale = "stale" in data ? data.stale : data.isStale;

  const candidateOf = useMemo(() => {
    const m = new Map<string, CellCandidate>();
    for (const c of data.candidates) m.set(c.key, c);
    return m;
  }, [data.candidates]);

  const columns = useMemo<Column<CellRow>[]>(
    () => [
      { id: "sample", label: "Sample", width: "64px", cell: (c) => `s${c.sample_id}` },
      {
        id: "candidate",
        label: "Candidate",
        width: "minmax(72px, 1fr)",
        cell: (c) => candidateOf.get(c.candidate)?.label ?? "—",
      },
      {
        id: "status",
        label: "Mark",
        width: "56px",
        cell: (c) => (
          <span className={cx("ms-status", `is-${c.status.toLowerCase()}`)}>{c.status}</span>
        ),
      },
      {
        id: "fitness",
        label: "Fitness",
        width: "64px",
        align: "end",
        cell: (c) => <Fitness value={c.fitness} />,
      },
      {
        id: "predicted",
        label: "Predicted",
        width: "minmax(160px, 3fr)",
        cell: (c) => <span title={c.predicted}>{c.predicted || "—"}</span>,
      },
      {
        id: "seconds",
        label: "Seconds",
        width: "72px",
        align: "end",
        cell: (c) => (c.seconds == null ? "—" : c.seconds.toFixed(1)),
      },
      {
        id: "tokens",
        label: "Tokens in/out",
        width: "112px",
        align: "end",
        cell: (c) =>
          c.input_tokens == null && c.output_tokens == null
            ? "—"
            : `${c.input_tokens ?? "–"}/${c.output_tokens ?? "–"}`,
      },
      { id: "cached", label: "", width: "24px", cell: (c) => (c.cached ? "↺" : "") },
    ],
    [candidateOf],
  );

  const groups = useMemo<RowGroup<CellRow>[] | undefined>(() => {
    if (groupBy === "none") return undefined;
    const by = new Map<string, CellRow[]>();
    const keyOf = groupBy === "sample" ? (c: CellRow) => String(c.sample_id) : (c: CellRow) => c.candidate;
    for (const c of data.cells) {
      const k = keyOf(c);
      const list = by.get(k);
      if (list) list.push(c);
      else by.set(k, [c]);
    }
    if (groupBy === "sample")
      return data.items
        .filter((it) => !hideUnmeasured || it.n_measured > 0)
        .map((it) => {
          const rows = by.get(String(it.sample_id)) ?? [];
          return { key: String(it.sample_id), header: <SampleHeader it={it} n={rows.length} />, rows };
        });
    return data.candidates.map((c) => {
      const rows = by.get(c.key) ?? [];
      return { key: c.key, header: <CandidateHeader c={c} n={rows.length} />, rows };
    });
  }, [groupBy, data.cells, data.items, data.candidates, hideUnmeasured]);

  // Folds ignored, so J/K stepping never skips a cell.
  const walk = useMemo(
    () => (groups ? groups.flatMap((g) => g.rows) : data.cells),
    [groups, data.cells],
  );
  const openIdx =
    owns && openCell
      ? walk.findIndex((c) => c.run_id === openCell.runId && c.sample_id === openCell.sampleId)
      : -1;
  const activeRowId = openIdx >= 0 ? cellId(walk[openIdx]!) : null;

  const open = (c: CellRow) => {
    const addr: CellAddress = { runId: c.run_id, sampleId: c.sample_id };
    setOpenCell(addr, paneId);
  };

  const totals = data.totals;
  const summary = [
    `${data.items.length} samples`,
    totals ? `${totals.total_measurements} graded cells` : null,
    totals?.mean_fitness != null ? `${fmtPct0(totals.mean_fitness)} mean fitness` : null,
    `${data.measuredCount} measured · ${data.unmeasuredCount} unmeasured`,
    data.splitTest != null ? `${data.splitTest} test held out` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  // The key the rows were ranked by, off the server's echo — never the pick, which moves first.
  const rankedBy: HardSampleOrder | null = ownRead ? own.order : shared.rankedBy;

  return (
    <section className={cx("ms-pane", stale && "is-stale")} aria-label="Measurements">
      {heading}
      <Toolbar className="ms-toolbar">
        <SegmentedControl ariaLabel="Group by" value={groupBy} onChange={setGroupBy} options={GROUPS} />
        <SegmentedControl ariaLabel="Cell mark" value={status} onChange={setStatus} options={STATUSES} />
        {preset.scope === undefined && (
          <SegmentedControl
            ariaLabel="Data scope"
            value={shared.scope}
            onChange={shared.setScope}
            options={[
              { value: "campaign", label: "This campaign" },
              { value: "dataset", label: "All campaigns" },
            ]}
          />
        )}
        {rankedBy && groupBy === "sample" && (
          <SegmentedControl
            ariaLabel="Ranking key"
            value={shared.rankedByPick ?? rankedBy}
            onChange={shared.setRankedBy}
            options={[
              { value: "info_gain", label: "Info gain", title: "Rank by expected decision-information gain." },
              { value: "difficulty", label: "Hardness", title: "Rank by fitted difficulty (δ), hardest first." },
            ]}
          />
        )}
        {groupBy === "sample" && (
          <label className="ms-check">
            <input
              type="checkbox"
              checked={hideUnmeasured}
              onChange={() => setHideUnmeasured((v) => !v)}
            />
            Hide unmeasured
          </label>
        )}
        <ToolbarSpacer />
      </Toolbar>
      <p className="ms-summary">
        {datasetName ? `${datasetName} · ` : ""}
        {summary}
      </p>
      {error ? (
        <ErrorNote>Couldn’t read the measurements: {error}</ErrorNote>
      ) : (
        <DataTable
          ariaLabel="Measured cells"
          columns={columns}
          rows={groups ? undefined : data.cells}
          groups={groups}
          getRowId={cellId}
          activeRowId={activeRowId}
          onRowClick={open}
          expandedByDefault={groupBy !== "sample"}
          empty={stale ? "Loading…" : "No measured cells here yet."}
        />
      )}
      {owns && openCell && datasetName && (
        <CellPanel
          datasetName={datasetName}
          cell={openCell}
          onClose={() => setOpenCell(null)}
          hasPrev={openIdx > 0}
          hasNext={openIdx >= 0 && openIdx < walk.length - 1}
          onStep={(shift) => {
            const next = walk[openIdx + shift];
            if (next) open(next);
          }}
        />
      )}
    </section>
  );
}
