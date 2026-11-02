"use client";
import { useMemo, useState, type ReactNode } from "react";
import type {
  CellCandidate,
  CellRow,
  CellStatus,
  DatasetItem,
  HardSampleOrder,
  HardSamplesScope,
} from "@/lib/api";
import {
  HARD_SAMPLE_ORDER_LABELS,
  HEATMAP_SCOPE_LABELS,
  SAMPLE_STATUS_LABELS,
} from "@/lib/api/types.generated";
import { fmtLogits, fmtUnit, PLACEHOLDER } from "@/lib/format";
import { cx } from "@/lib/cx";
import { useHardSamples, usePresetCells, type CellsPreset } from "@/lib/hard-samples";
import { useWorkspace } from "@/lib/workspace";
import {
  Chip,
  DataTable,
  FailureLine,
  IconMore,
  Menu,
  MenuCheck,
  MenuRadioGroup,
  MenuSep,
  SegmentedControl,
  Toolbar,
  ToolbarSpacer,
  type Column,
  type RowGroup,
} from "@/components/ui";
import { CellPanel, Fitness, Verdict } from "./CellPanel";

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
const STATUSES: { value: StatusPick; label: string }[] = [
  { value: "all", label: "All" },
  ...(Object.keys(SAMPLE_STATUS_LABELS) as CellStatus[]).map((value) => ({
    value,
    label: SAMPLE_STATUS_LABELS[value],
  })),
];

// The pickable scopes; `cycle` is a preset's alone.
const SCOPES = (["campaign", "dataset"] as const).map((value) => ({
  value,
  label: HEATMAP_SCOPE_LABELS[value],
}));

const ORDERS = (Object.keys(HARD_SAMPLE_ORDER_LABELS) as HardSampleOrder[]).map((value) => ({
  value,
  label: HARD_SAMPLE_ORDER_LABELS[value],
}));

const cellId = (c: CellRow) => `${c.candidate}\u001f${c.position}`;

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

// `cell` is the Samples view's key: that one mount opens a cell, and a row anywhere opens it there.
export function MeasurementsPane({
  preset = {},
  cell = null,
  heading,
}: {
  preset?: MeasurementsPreset;
  cell?: string | null;
  heading?: ReactNode;
}) {
  const shared = useHardSamples();
  const { open: openView } = useWorkspace();
  const [groupBy, setGroupBy] = useState<GroupBy>(preset.groupBy ?? "none");
  const [status, setStatus] = useState<StatusPick>("all");
  const [hideUnmeasured, setHideUnmeasured] = useState(false);

  const data = usePresetCells(preset, status === "all" ? undefined : status);
  const { datasetName, failure, stale } = data;

  const candidateOf = useMemo(() => {
    const m = new Map<string, CellCandidate>();
    for (const c of data.candidates) m.set(c.key, c);
    return m;
  }, [data.candidates]);

  // A group's header already names what every row under it shares; fixed tracks, so each row's grid
  // lines up with the header's, and the predicted answer takes what is left, truncated.
  const columns = useMemo<Column<CellRow>[]>(() => {
    const all: (Column<CellRow> & { id: GroupBy | "fitness" | "predicted" })[] = [
      { id: "sample", label: "Sample", width: "5ch", cell: (c) => `s${c.position}` },
      {
        id: "candidate",
        label: "Candidate",
        width: "minmax(0, 10ch)",
        cell: (c) => {
          const label = candidateOf.get(c.candidate)?.label ?? PLACEHOLDER;
          return <span title={label}>{label}</span>;
        },
      },
      { id: "fitness", label: "Fitness", width: "6.5rem", cell: (c) => <Verdict cell={c} /> },
      {
        id: "predicted",
        label: "Predicted",
        width: "minmax(0, 1fr)",
        cell: (c) => <span title={c.predicted}>{c.predicted || PLACEHOLDER}</span>,
      },
    ];
    return all.filter((c) => c.id !== groupBy);
  }, [candidateOf, groupBy]);

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
  const openIdx = cell === null ? -1 : walk.findIndex((c) => c.answer === cell);
  const activeRowId = openIdx >= 0 ? cellId(walk[openIdx]!) : null;

  const open = (c: CellRow) => openView("samples", c.answer);

  // The key the rows were ranked by, off the server's echo — never the pick, which moves first.
  const { rankedBy } = data;
  const scope: HardSamplesScope = preset.scope ?? shared.scope;
  const ranked = groupBy === "sample" ? rankedBy : null;

  const totals = data.totals;
  // Every choice that moves the numbers below is named here, so none hides behind the ⋯ menu.
  const summary = [
    datasetName,
    HEATMAP_SCOPE_LABELS[scope],
    groupBy === "none" ? null : `by ${groupBy}`,
    ranked ? `ranked by ${HARD_SAMPLE_ORDER_LABELS[ranked].toLowerCase()}` : null,
    `${data.items.length} samples`,
    totals ? `${totals.total_measurements} graded cells` : null,
    totals?.mean_fitness != null ? `${fmtUnit("rate", totals.mean_fitness)} mean fitness` : null,
    `${data.measuredCount} measured · ${data.unmeasuredCount} unmeasured`,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <section className={cx("ms-pane", stale && "is-stale")} aria-label="Measurements">
      {heading}
      <Toolbar>
        <SegmentedControl ariaLabel="Cell status" value={status} onChange={setStatus} options={STATUSES} />
        <ToolbarSpacer />
        <Menu
          renderTrigger={({ open, toggle }) => (
            <Chip icon on={open} expanded={open} ariaLabel="More measurement options" onClick={toggle}>
              <IconMore />
            </Chip>
          )}
        >
          {({ close }) => (
            <>
              <MenuRadioGroup
                label="Group by"
                value={groupBy}
                options={GROUPS}
                onChange={(v) => {
                  setGroupBy(v);
                  close();
                }}
              />
              {preset.scope === undefined && (
                <>
                  <MenuSep />
                  <MenuRadioGroup
                    label="Data scope"
                    value={shared.scope}
                    options={SCOPES}
                    onChange={(v) => {
                      shared.setScope(v);
                      close();
                    }}
                  />
                </>
              )}
              {ranked && (
                <>
                  <MenuSep />
                  <MenuRadioGroup
                    label="Rank samples by"
                    value={shared.rankedByPick ?? ranked}
                    options={ORDERS}
                    onChange={(v) => {
                      shared.setRankedBy(v);
                      close();
                    }}
                  />
                </>
              )}
              {groupBy === "sample" && (
                <>
                  <MenuSep />
                  <MenuCheck on={hideUnmeasured} onClick={() => setHideUnmeasured((v) => !v)}>
                    Hide unmeasured
                  </MenuCheck>
                </>
              )}
            </>
          )}
        </Menu>
      </Toolbar>
      <p className="ms-summary">{summary}</p>
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
      {cell !== null && datasetName && (
        <CellPanel
          datasetName={datasetName}
          answer={cell}
          onClose={() => openView("samples", null)}
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
