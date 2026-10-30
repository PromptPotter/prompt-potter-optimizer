"use client";
import { useEffect, useId, useMemo, useState, type ReactNode } from "react";
import type { CellCandidate, CellRow, CellStatus, DatasetItem } from "@/lib/api";
import type { CellAddress } from "@/lib/address";
import { fmtLogits, fmtUnit, PLACEHOLDER } from "@/lib/format";
import { cx } from "@/lib/cx";
import { fitnessStyle } from "@/lib/derivations";
import { useHardSamples, usePresetCells, type CellsPreset } from "@/lib/hard-samples";
import { useWorkspace } from "@/lib/workspace";
import {
  DataTable,
  FailureLine,
  SegmentedControl,
  Toolbar,
  ToolbarSpacer,
  type Column,
  type RowGroup,
} from "@/components/ui";
import { CellPanel } from "./CellPanel";

type GroupBy = "sample" | "candidate" | "none";

interface MeasurementsPreset extends CellsPreset {
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

const cellId = (c: CellRow) => `${c.candidate}\u001f${c.position}`;

function Fitness({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="ms-dim">—</span>;
  return (
    <span className="ms-fit" style={fitnessStyle(value)}>
      {fmtUnit("rate", value)}
    </span>
  );
}

function SampleHeader({ it, n }: { it: DatasetItem; n: number }) {
  return (
    <span className="ms-group-line">
      <span className="ms-rank">#{it.hard_sample_rank}</span>
      <span className="ms-sid">s{it.position}</span>
      <span className="ms-query" title={it.query}>
        {it.query}
      </span>
      <span className="ms-dim">
        {it.n_measured}/{n} graded
      </span>
      <Fitness value={it.mean_fitness} />
      <span className="ms-dim" title="Difficulty δ on the ruler (higher = harder)">
        {it.delta == null ? it.delta_label : `δ ${fmtLogits(it.delta)}`}
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
  claimsAddress = false,
  heading,
}: {
  preset?: MeasurementsPreset;
  claimsAddress?: boolean;
  heading?: ReactNode;
}) {
  const shared = useHardSamples();
  const { openCell, openCellOwner, setOpenCell, releaseCell } = useWorkspace();
  const paneId = useId();
  const [groupBy, setGroupBy] = useState<GroupBy>(preset.groupBy ?? "none");
  const [status, setStatus] = useState<StatusPick>("all");
  const [hideUnmeasured, setHideUnmeasured] = useState(false);
  const owns = openCellOwner === paneId || (openCellOwner === null && claimsAddress);
  useEffect(() => () => releaseCell(paneId), [releaseCell, paneId]);

  const data = usePresetCells(preset, status === "all" ? undefined : status);
  const { datasetName, failure, stale } = data;

  const candidateOf = useMemo(() => {
    const m = new Map<string, CellCandidate>();
    for (const c of data.candidates) m.set(c.key, c);
    return m;
  }, [data.candidates]);

  const columns = useMemo<Column<CellRow>[]>(
    () => [
      { id: "sample", label: "Sample", width: "64px", cell: (c) => `s${c.position}` },
      {
        id: "candidate",
        label: "Candidate",
        width: "minmax(72px, 1fr)",
        cell: (c) => candidateOf.get(c.candidate)?.label ?? PLACEHOLDER,
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
        cell: (c) => <span title={c.predicted}>{c.predicted || PLACEHOLDER}</span>,
      },
      {
        id: "seconds",
        label: "Seconds",
        width: "72px",
        align: "end",
        cell: (c) => fmtUnit("seconds", c.seconds),
      },
      {
        id: "tokens",
        label: "Tokens in/out",
        width: "112px",
        align: "end",
        cell: (c) =>
          c.input_tokens == null && c.output_tokens == null
            ? PLACEHOLDER
            : `${c.input_tokens ?? "–"}/${c.output_tokens ?? "–"}`,
      },
      { id: "cached", label: "", width: "24px", cell: (c) => (c.cached ? "↺" : "") },
    ],
    [candidateOf],
  );

  const groups = useMemo<RowGroup<CellRow>[] | undefined>(() => {
    if (groupBy === "none") return undefined;
    const by = new Map<string, CellRow[]>();
    const keyOf = groupBy === "sample" ? (c: CellRow) => String(c.position) : (c: CellRow) => c.candidate;
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
          const rows = by.get(String(it.position)) ?? [];
          return { key: String(it.position), header: <SampleHeader it={it} n={rows.length} />, rows };
        });
    return data.candidates.map((c) => {
      const rows = by.get(c.key) ?? [];
      return { key: c.key, header: <CandidateHeader c={c} n={rows.length} />, rows };
    });
  }, [groupBy, data.cells, data.items, data.candidates, hideUnmeasured]);

  const walk = useMemo(
    () => (groups ? groups.flatMap((g) => g.rows) : data.cells),
    [groups, data.cells],
  );
  const openIdx =
    owns && openCell
      ? walk.findIndex((c) => c.answer === openCell.answer)
      : -1;
  const activeRowId = openIdx >= 0 ? cellId(walk[openIdx]!) : null;

  const open = (c: CellRow) => {
    const addr: CellAddress = { answer: c.answer };
    setOpenCell(addr, paneId);
  };

  const totals = data.totals;
  const summary = [
    `${data.items.length} samples`,
    totals ? `${totals.total_measurements} graded cells` : null,
    totals?.mean_fitness != null ? `${fmtUnit("rate", totals.mean_fitness)} mean fitness` : null,
    `${data.measuredCount} measured · ${data.unmeasuredCount} unmeasured`,
  ]
    .filter(Boolean)
    .join(" · ");

  // The key the rows were ranked by, off the server's echo — never the pick, which moves first.
  const { rankedBy } = data;

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
              { value: "difficulty", label: "Hardness", title: "Rank by the ruler's difficulty (δ), hardest first." },
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
      {failure !== null ? (
        <FailureLine failure={failure} lead="Couldn’t read the measurements." />
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
