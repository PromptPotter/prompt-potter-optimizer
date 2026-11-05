import { useMemo, useState, type ReactNode } from "react";
import {
  type CellCandidate,
  type CellRow,
  type CellStatus,
  type DatasetItem,
  type HardSamplesScope,
  HARD_SAMPLE_ORDER_LABELS,
  HEATMAP_SCOPE_LABELS,
  SAMPLE_STATUS_LABELS,
} from "@/lib/api/types";
import { wordKeys } from "@/lib/api/words";
import { fmtLogits, fmtUnit, PLACEHOLDER } from "@/lib/format";
import { cx } from "@/lib/cx";
import { setCellsView, useCells, useCellsView, type CellsPreset } from "@/lib/hooks/useCells";
import { failureOf, shownData } from "@/lib/hooks/useRead";
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
  ...wordKeys(SAMPLE_STATUS_LABELS).map((value) => ({
    value,
    label: SAMPLE_STATUS_LABELS[value],
  })),
];

// The pickable scopes; `cycle` is a preset's alone.
const SCOPES = (["campaign", "dataset"] as const).map((value) => ({
  value,
  label: HEATMAP_SCOPE_LABELS[value],
}));

const ORDERS = wordKeys(HARD_SAMPLE_ORDER_LABELS).map((value) => ({
  value,
  label: HARD_SAMPLE_ORDER_LABELS[value],
}));

const NO_SAMPLES: DatasetItem[] = [];
const NO_CANDIDATES: CellCandidate[] = [];
const NO_CELLS: CellRow[] = [];

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
  const view = useCellsView();
  const { open: openView } = useWorkspace();
  const [groupBy, setGroupBy] = useState<GroupBy>(preset.groupBy ?? "none");
  const [status, setStatus] = useState<StatusPick>("all");
  const [hideUnmeasured, setHideUnmeasured] = useState(false);

  const { datasetName, read } = useCells(preset, status === "all" ? undefined : status);
  const body = shownData(read);
  const failure = failureOf(read);
  const { samples, candidates, cells } = useMemo(
    () => ({
      samples: body?.samples ?? NO_SAMPLES,
      candidates: body?.candidates ?? NO_CANDIDATES,
      cells: body?.cells ?? NO_CELLS,
    }),
    [body],
  );

  const candidateOf = useMemo(() => {
    const m = new Map<string, CellCandidate>();
    for (const c of candidates) m.set(c.key, c);
    return m;
  }, [candidates]);

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
    for (const c of cells) {
      const k = keyOf(c);
      const list = by.get(k);
      if (list) list.push(c);
      else by.set(k, [c]);
    }
    if (groupBy === "sample")
      return samples
        .filter((it) => !hideUnmeasured || it.n_measured > 0)
        .map((it) => {
          const rows = by.get(String(it.position)) ?? [];
          return { key: String(it.position), header: <SampleHeader it={it} n={rows.length} />, rows };
        });
    return candidates.map((c) => {
      const rows = by.get(c.key) ?? [];
      return { key: c.key, header: <CandidateHeader c={c} n={rows.length} />, rows };
    });
  }, [groupBy, cells, samples, candidates, hideUnmeasured]);

  const walk = useMemo(
    () => (groups ? groups.flatMap((g) => g.rows) : cells),
    [groups, cells],
  );
  const openIdx = cell === null ? -1 : walk.findIndex((c) => c.answer === cell);
  const activeRowId = openIdx >= 0 ? cellId(walk[openIdx]!) : null;

  const open = (c: CellRow) => openView("samples", c.answer);

  // The key the rows were ranked by, off the server's echo — never the pick, which moves first.
  const scope: HardSamplesScope = preset.scope ?? view.scope;
  const ranked = groupBy === "sample" ? (body?.order ?? null) : null;

  // Every choice that moves the numbers below is named here, so none hides behind the ⋯ menu.
  const summary = [
    datasetName,
    HEATMAP_SCOPE_LABELS[scope],
    groupBy === "none" ? null : `by ${groupBy}`,
    ranked ? `ranked by ${HARD_SAMPLE_ORDER_LABELS[ranked].toLowerCase()}` : null,
    `${samples.length} samples`,
    body ? `${body.total_measurements} graded cells` : null,
    body?.mean_fitness != null ? `${fmtUnit("rate", body.mean_fitness)} mean fitness` : null,
    `${body?.measured_samples ?? 0} measured · ${body?.unmeasured_samples ?? 0} unmeasured`,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <section className={cx("ms-pane", read.status === "loading" && "is-stale")} aria-label="Measurements">
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
                    value={view.scope}
                    options={SCOPES}
                    onChange={(v) => {
                      setCellsView({ ...view, scope: v });
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
                    value={view.rankedBy ?? ranked}
                    options={ORDERS}
                    onChange={(v) => {
                      setCellsView({ ...view, rankedBy: v });
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
          rows={groups ? undefined : cells}
          groups={groups}
          getRowId={cellId}
          activeRowId={activeRowId}
          onRowClick={open}
          expandedByDefault={groupBy !== "sample"}
          empty={read.status === "ready" ? "No measured cells here yet." : "Loading…"}
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
