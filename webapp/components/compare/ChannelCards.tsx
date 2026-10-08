"use client";
// The Compare LIST, one item per channel, folding per item; campaigns alone read as COLUMNS of a
// transposed table (`campaignColumns`), each folding to a panel below it. Every number is served.

import { useCallback, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { CELL_MEAN_ROWS } from "@/lib/cell-means";
import type {
  Evidence,
  HeadToHead,
  HeadToHeadRow,
  LineageNode,
  MeteredSpend,
  SubjectReading,
} from "@/lib/api";
import { STOP_REASON_LABELS } from "@/lib/api/types.generated";
import { useCampaignPipeline } from "@/lib/hooks/useConnector";
import { candidateSubject, readingPath } from "@/lib/api/reads";
import { SteerForkAction } from "@/components/shell/searchpoint/SteerForkAction";
import { VerifyAction } from "@/components/shell/searchpoint/VerifyAction";
import {
  Forest,
  type CladogramChannel,
  type CladogramCtx,
} from "@/components/candidates/Forest";
import { DENSE, type RoundNodePos } from "@/components/candidates/forest-layout";
import { SearchpointDrillIn } from "@/components/shell/searchpoint/SearchpointDrillIn";
import { MeasurementsPane } from "@/components/shell/measurements/MeasurementsPane";
import type { CompareChannel } from "@/lib/compare-selection";
import {
  applyFlatEdits,
  BENCH_STAT_LABEL,
  SPEND_STAT_LABEL,
  benchReading,
  campaignCard,
  campaignColumns,
  campaignStatus,
  candidateObserveConfig,
  compareItems,
  descendantsOf,
  historicalSamplesFor,
  indexLineage,
  mainLine,
  nodeKeyOf,
  nodeOverlays,
  pathOf,
  pipelineReadStatus,
  scoreboardRow,
  searchpointCopyChoices,
  selectedNodeOf,
  sharedComparableNote,
  spendStat,
  walkCourses,
  type LineageIndex,
  type MainLineStep,
  type RunGroup,
} from "@/lib/derivations";
import { readyData } from "@/lib/hooks/useRead";
import { useRoundFile } from "@/lib/hooks/useRoundFile";
import { useLineageTree } from "@/lib/lineage";
import { cx } from "@/lib/cx";
import {
  sideTone,
  fmtDuration,
  fmtMetricInterval,
  fmtMetricValue,
  fmtPct0,
  fmtSigned,
  fmtUsd,
  shortId,
  verdictTone,
} from "@/lib/format";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";
import { seriesVar } from "@/lib/theme";
import { SummaryBlock } from "@/components/shell/SummaryBlock";
import {
  ChannelRestore,
  editsFor,
  pointKeyOf,
  withOverlay,
  type ScenarioEdits,
} from "./config-edit";
import { Badge, CopyButton, SegmentedControl, Term } from "@/components/ui";

const KIND_WORD: Record<SubjectReading["kind"], string> = {
  campaign: "origin",
  course: "branch head",
  candidate: "searchpoint",
};

// `inside` + the three ids are the address a tree node publishes as `coursePathKey` + `candidateId`.
function channelPoints(subjects: readonly SubjectReading[]): CladogramChannel[] {
  return subjects.map((s, i) => ({
    coursePathKey: encodeCyclePath(readingPath(s)),
    candidateId: s.candidate_id,
    // Served order — the index the bars, legend and pairwise table read, so one channel is one colour.
    ink: seriesVar(i),
  }));
}

export function ChannelCards({
  evidence,
  channels,
  runs,
  edits,
  onEdits,
  onReplace,
  onAdd,
  hasSubject,
  onRemove,
}: {
  evidence: Evidence;
  // Channels whose configuration was edited: their card withdraws its level (`config-edit.tsx`).
  edits: ScenarioEdits;
  onEdits: (next: ScenarioEdits) => void;
  // Read off the request, not the response, so a channel that answered nothing still gets a card.
  channels: readonly CompareChannel[];
  // The sidebar's own campaign reading, by campaign id: a column's header and live state.
  runs: ReadonlyMap<string, RunGroup>;
  onReplace: (from: string, to: string) => void;
  onAdd: (channel: CompareChannel) => void;
  hasSubject: (subject: string) => boolean;
  onRemove: (subject: string) => void;
}) {
  const items = useMemo(() => compareItems(evidence, channels), [evidence, channels]);
  const listNote = useMemo(() => sharedComparableNote(items), [items]);
  const points = useMemo(() => channelPoints(evidence.subjects), [evidence.subjects]);
  const h2h = evidence.head_to_head;
  const columns = useMemo(() => campaignColumns(items), [items]);
  const rows = useMemo(() => {
    if (!columns) return null;
    const flagged = items.some(
      ({ headline }) => headline && (!headline.controlled || headline.comparable !== true),
    );
    return COLUMN_ROWS.filter(
      (r) => (r !== "optimizer" || columns.showOptimizer) && (r !== "standing" || flagged),
    );
  }, [columns, items]);
  // The folded panels land BELOW the table: a column is too narrow to hold a drill-in.
  const [panelHost, setPanelHost] = useState<HTMLDivElement | null>(null);
  const cards = items.map(({ channel, reading, slot, headline }, i) => (
    <ChannelCard
      key={channel.subject}
      channel={channel}
      reading={reading}
      headline={headline}
      ownNote={listNote === null}
      points={points}
      // Null for an unread channel: it plots no series, so any ink here would be another channel's.
      own={slot === null ? null : (points[slot] ?? null)}
      edits={edits}
      onEdits={onEdits}
      unit={evidence.metric.spec.unit}
      axis={evidence.metric.spec.axis_label}
      onReplace={onReplace}
      onAdd={onAdd}
      hasSubject={hasSubject}
      onRemove={onRemove}
      run={runs.get(channel.rootCampaignId) ?? null}
      uncontrolledNote={h2h === null ? null : h2h.uncontrolled_note}
      column={rows && { rows, panelHost, order: i }}
    />
  ));
  return (
    <>
      {h2h && <HeadToHeadVerdict h2h={h2h} scorerId={evidence.scorer_id} />}
      {/* Neutral once it holds for every item: nothing on the list then reads against another. */}
      {listNote !== null && (
        <p className="l4-note">
          <strong>Every channel below</strong> — {listNote}
        </p>
      )}
      {rows ? (
        <>
          <div
            className="cmp-cols"
            style={{ "--cmp-rows": rows.length } as CSSProperties}
            role="group"
            aria-label="Campaigns side by side"
          >
            <div className="cmp-col">
              {rows.map((r) => (
                <div key={r} className={cx("cmp-cell", "cmp-row-label", `is-${r}`)}>
                  {ROW_LABEL[r]}
                </div>
              ))}
            </div>
            {cards}
          </div>
          <div className="cmp-col-panels" ref={setPanelHost} />
        </>
      ) : (
        <ol className="cmp-channels">{cards}</ol>
      )}
    </>
  );
}

const COLUMN_ROWS = [
  "head",
  "optimizer",
  "bench",
  "lift",
  "per_usd",
  "spent",
  "worked",
  "standing",
  "reads",
  "line",
  "more",
] as const;
type ColumnRow = (typeof COLUMN_ROWS)[number];

const ROW_LABEL: Record<ColumnRow, ReactNode> = {
  head: null,
  optimizer: "optimizer",
  bench: "bench",
  lift: "lift over origin",
  per_usd: "lift / USD",
  spent: "spent",
  worked: "worked",
  standing: "standing",
  reads: "reads at",
  line: (
    <Term
      content={
        <p className="cmp-line-key">
          Each round&rsquo;s pick, from the origin up to the searchpoint above: its accuracy on the
          cells it measured, then its lift over its parent on those same cells, toned by the 95%
          interval. The lift is the round&rsquo;s verdict — the accuracy moves with the subset each
          round drew. A held round crowned nobody, so the line kept its parent.
        </p>
      }
    >
      main line
    </Term>
  ),
  more: null,
};

// Every sentence is served; why a row is not controlled rides that row's badge, not this block.
function HeadToHeadVerdict({ h2h, scorerId }: { h2h: HeadToHead; scorerId: string }) {
  return (
    <div className="cmp-verdict">
      <p className={cx("cmp-verdict-line", verdictTone(h2h.verdict))}>
        <strong>Bench head-to-head{h2h.head_to_head_id ? ` ${h2h.head_to_head_id}` : ""}</strong>
        {" — "}
        {h2h.verdict_line}
      </p>
      <p className="cmp-verdict-meta">
        {h2h.differs_on.length > 0 && (
          <span className="cmp-channel-badges" role="group" aria-label="Bench set differs on">
            <span className="l4-dim">differs on</span>
            {h2h.differs_on.map((field) => (
              <Badge key={field} tone="danger">
                {field}
              </Badge>
            ))}
          </span>
        )}
        <span className="l4-dim">
          graded by <code className="cmp-verdict-scorer">{scorerId}</code>
        </span>
      </p>
      {h2h.notes.length > 0 && (
        <ul className="cmp-verdict-notes">
          {h2h.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

// The column's main line to its picked head; every number on a step is the tree's.
function MainLine({ steps }: { steps: readonly MainLineStep[] }) {
  if (steps.length === 0) return <span className="l4-dim">—</span>;
  return (
    <ol className="cmp-line">
      {steps.map((s) =>
        s.kind === "held" ? (
          <li key={`held-${s.round}`} className="cmp-line-step is-held">
            <span className="cmp-line-round">R{s.round}</span>
            <span className="cmp-line-label">held</span>
            <span className="cmp-line-change">kept its parent</span>
          </li>
        ) : (
          <li
            key={`${encodeCyclePath(pathOf(s.node))}/${s.node.id}`}
            className={cx("cmp-line-step", s.node.is_selected && "is-crowned")}
          >
            <span className="cmp-line-round">R{s.node.round ?? 0}</span>
            <span className="cmp-line-label">{s.node.label}</span>
            <span className="cmp-line-score">
              {fmtPct0(s.node.accuracy)}
              {s.node.reference_lift !== null && (
                <span className={sideTone(s.node.reference_lift_side)}>
                  {" "}
                  {fmtSigned(s.node.reference_lift, 2)}
                </span>
              )}
            </span>
            {/* Round 0's text is the origin's provenance, not a change. */}
            <span className="cmp-line-change" title={s.node.changes_description || undefined}>
              {s.node.round === 0 ? "origin" : s.node.changes_description || "—"}
            </span>
          </li>
        ),
      )}
    </ol>
  );
}

function Metric({
  label,
  value,
  band,
  tone,
}: {
  label?: string;
  value: string;
  band: string;
  tone?: string;
}) {
  return (
    <div className={cx("cmp-metric", tone)}>
      {label && <span className="cmp-metric-label">{label}</span>}
      <span className="cmp-metric-num l4-effect-mean">{value}</span>
      {/* One line: a stopped pass's served reason can be a provider's whole error. */}
      <span className="cmp-metric-band" title={band}>
        {band}
      </span>
    </div>
  );
}

// The BENCH headline off the served head-to-head row, never the search rows; a missing one says why
// in the sidebar's own words (`benchReading`).
function benchLead(row: HeadToHeadRow | null) {
  const bench = row?.bench ?? null;
  const s = bench?.selected ?? null;
  const level = s?.[s.headline] ?? null;
  const lift = row?.headline_lift ?? null;
  return {
    score:
      bench === null || s === null
        ? { value: "—", band: benchReading(bench, row?.bench_missing_reason).sub ?? "" }
        : {
            value: fmtMetricValue("level", level?.value ?? null),
            band: `${s.headline} ${fmtMetricInterval("level", level?.ci_lo ?? null, level?.ci_hi ?? null)} · ${s.n_scored}/${bench.bench_size} rows`,
          },
    lift:
      lift === null
        ? { value: "—", band: "" }
        : {
            value: fmtSigned(lift.value),
            band: fmtMetricInterval("delta", lift.ci_lo, lift.ci_hi),
            tone: sideTone(lift.side),
          },
  };
}

// What the arm's budget counts — the sidebar's own spend stat — its buckets on a secondary line.
function SpentReading({ metered }: { metered: MeteredSpend | null }) {
  if (metered === null) return "—";
  const { value, sub } = spendStat(metered);
  return (
    <span className="cmp-metric">
      {value}
      <span className="cmp-metric-band" title={sub}>
        {sub}
      </span>
    </span>
  );
}

function CellMeans({ means }: { means: Record<string, number> | undefined }) {
  const rows = CELL_MEAN_ROWS.flatMap((r) => {
    const v = means?.[r.key];
    return v === undefined ? [] : [{ ...r, v }];
  });
  if (rows.length === 0) return null;
  return (
    <dl className="cmp-channel-facts cmp-channel-means">
      {rows.map((r) => (
        <div key={r.key}>
          <dt>{r.label}</dt>
          <dd>{r.fmt(r.v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function CostFacts({ row }: { row: HeadToHeadRow }) {
  return (
    <dl className="cmp-channel-facts cmp-channel-cost">
      <div>
        <dt>lift / USD</dt>
        <dd>{fmtSigned(row.lift_per_incurred_usd, 2)}</dd>
      </div>
      <div>
        <dt>spent</dt>
        <dd>
          <SpentReading metered={row.spend_metered} />
        </dd>
      </div>
      <div>
        <dt>worked</dt>
        <dd>{row.worked_s === null ? "—" : fmtDuration(row.worked_s)}</dd>
      </div>
      <div>
        <dt>calls</dt>
        <dd>{row.calls ?? "—"}</dd>
      </div>
      <div>
        <dt>replayed</dt>
        <dd>{row.spend_metered === null ? "—" : fmtPct0(row.spend_metered.replay_share)}</dd>
      </div>
    </dl>
  );
}

// The shared instrument is the expected state, so only its absence wears a badge.
function HeadlineBadges({
  row,
  uncontrolledNote,
}: {
  row: HeadToHeadRow;
  uncontrolledNote: string | null;
}) {
  return (
    <span className="cmp-channel-badges">
      {row.controlled ? (
        <Badge>controlled</Badge>
      ) : uncontrolledNote === null ? (
        <Badge tone="danger">not controlled</Badge>
      ) : (
        <Term content={uncontrolledNote}>
          <Badge tone="danger">not controlled</Badge>
        </Term>
      )}
      {row.comparable !== true && (
        <Badge tone={row.comparable === false ? "danger" : "default"}>
          {row.comparable === null ? "no headline" : "instrument off"}
        </Badge>
      )}
      {row.outcome === "failed" && row.stop_reason !== null && (
        <Badge tone="danger">{STOP_REASON_LABELS[row.stop_reason]}</Badge>
      )}
    </span>
  );
}

function ChannelCard({
  channel,
  reading,
  headline,
  uncontrolledNote,
  ownNote,
  points,
  own,
  edits,
  onEdits,
  unit,
  axis,
  onReplace,
  onAdd,
  hasSubject,
  onRemove,
  column,
  run,
}: {
  channel: CompareChannel;
  reading: SubjectReading | null;
  headline: HeadToHeadRow | null;
  uncontrolledNote: string | null;
  // False where the list already said this item's `comparable_note` once for all of them.
  ownNote: boolean;
  points: readonly CladogramChannel[];
  own: CladogramChannel | null;
  edits: ScenarioEdits;
  onEdits: (next: ScenarioEdits) => void;
  unit: Parameters<typeof fmtMetricValue>[0];
  axis: string;
  onReplace: (from: string, to: string) => void;
  onAdd: (channel: CompareChannel) => void;
  hasSubject: (subject: string) => boolean;
  onRemove: (subject: string) => void;
  // Set when the list reads as campaign columns: this card renders one cell per row, in order.
  column: { rows: readonly ColumnRow[]; panelHost: HTMLElement | null; order: number } | null;
  run: RunGroup | null;
}) {
  const subject = channel.subject;
  const campaignName = run?.campaign.label || shortId(channel.rootCampaignId);
  // The TOP-LEVEL campaign (the registry lists no inner one), read off the registry — never parsed from
  // the subject: `lib/api/reads.ts` is the one place the browser spells the address grammar.
  const rootCycle = run?.root.cycle_id ?? null;
  const rootPath = useMemo<CyclePath>(
    () => (rootCycle ? [{ campaignId: channel.rootCampaignId, cycleId: rootCycle }] : []),
    [rootCycle, channel.rootCampaignId],
  );

  // One tree subscription per card: the head picker needs the genealogy before the map ever opens.
  const { root, loaded, failed } = useLineageTree(rootPath, rootPath.length > 0);
  const index = useMemo(() => indexLineage(root), [root]);
  // One slot, two writers (head picker, map click). It moves only the highlight; re-pointing the
  // channel is the explicit verb in `ArmedActions`.
  const [selected, setSelected] = useState<LineageNode | null>(null);
  const head = useChannelHead(index, reading, selected);
  // Render-phase seed: a `useEffect` would paint one frame of the previous channel's pick.
  // A column opens on the Winner: an optimizer is compared on its pick, not on the origin it read.
  const [seededFor, setSeededFor] = useState<string | null>(null);
  const seed = column ? head.nodeFor("winner") : head.own;
  if (seed && seededFor !== subject) {
    setSeededFor(subject);
    setSelected(seed);
  }
  const [mapOpen, setMapOpen] = useState(false);
  // Must NOT close when the pick moves — that is the moment the operator asked to see something.
  const [setupOpen, setSetupOpen] = useState(true);
  // A searchpoint channel is on the board to be READ, so it opens; a campaign's leads on its row.
  const [detailOpen, setDetailOpen] = useState(reading?.kind === "candidate");

  // No live snapshot: exactly one cycle streams (`webapp/CLAUDE.md` § Polling shape), so a round
  // still scoring has nothing to read here.
  const pickedPath = useMemo(() => (selected ? pathOf(selected) : null), [selected]);
  const { doc, loading: docLoading } = useRoundFile(
    detailOpen ? pickedPath : null,
    selected?.round ?? null,
  );
  // Addressed as the point, never by dataset name: one `pipeline.yaml` is shared by every campaign on the slug.
  const at = useMemo(
    () => (pickedPath && selected ? candidateSubject(pickedPath, selected.id) : ""),
    [pickedPath, selected],
  );
  const pickedCampaign = pickedPath?.at(-1)?.campaignId ?? "";
  const pipelineRead = useCampaignPipeline(detailOpen && at ? pickedCampaign || null : null, at);
  const pipeline = readyData(pipelineRead);
  const pipelineStatus = pipelineReadStatus({
    bound: pipelineRead.status !== "idle",
    loading: pipelineRead.status === "loading",
    failed: pipelineRead.status === "failed",
  });
  const { pickedArms, pickedIdx } = useMemo(() => {
    if (!selected) return { pickedArms: null, pickedIdx: 0 };
    const sibs = (index.get(encodeCyclePath(pathOf(selected)))?.candidates ?? []).filter(
      (c) => c.round === selected.round,
    );
    return { pickedArms: sibs.length || null, pickedIdx: Math.max(sibs.indexOf(selected), 0) };
  }, [index, selected]);
  const line = useMemo(
    () =>
      selected
        ? mainLine(index.get(encodeCyclePath(pathOf(selected)))?.candidates ?? [], selected)
        : [],
    [index, selected],
  );
  const docId = selected?.id ?? null;
  const pickedRow = selected
    ? scoreboardRow(doc, docId ?? "", selected.label, selected.round ?? 0, pickedIdx)
    : null;
  const pickedCfg = selected
    ? candidateObserveConfig(doc, selected.course_label, selected.label)
    : null;
  const pickedSamples = useMemo(
    () => (docId && selected ? historicalSamplesFor(doc, selected.round ?? 0, docId) : []),
    [doc, docId, selected],
  );
  const pickedKey = useMemo(
    () => (selected ? candidateSubject(pathOf(selected), selected.id) || subject : subject),
    [selected, subject],
  );
  const pickedIsOwn = !!reading && pickedKey === pointKeyOf(reading);
  // Indexed per round, never summed. The fold is this cycle's ledger only, so it answers only for a pick
  // on this cycle's lane; a fork's lane was billed to a file this read did not open.
  const spentTo = useMemo(() => {
    if (!reading || !selected || selected.round == null) return null;
    const onOwnCourse =
      encodeCyclePath(pathOf(selected)) === encodeCyclePath(readingPath(reading));
    return onOwnCourse ? (reading.spend_to_round[String(selected.round)] ?? null) : null;
  }, [reading, selected]);
  const pickedSeed = useMemo(
    () => applyFlatEdits(pickedCfg?.config ?? {}, editsFor(edits, pickedKey)),
    [pickedCfg, edits, pickedKey],
  );

  // Each node is asked for its own address rather than parsing edit keys apart — the subject grammar is
  // the server's (`webapp/CLAUDE.md` § Viewed identity — one address (CyclePath)).
  const edited = useMemo(() => {
    const seeds: string[] = [];
    for (const { candidates } of index.values()) {
      for (const c of candidates) {
        if (editsFor(edits, candidateSubject(pathOf(c), c.id)).size > 0) seeds.push(c.id);
      }
    }
    return seeds;
  }, [index, edits]);
  const invalidated = useMemo(() => descendantsOf(root, edited), [root, edited]);

  // An edit removes the ground under the level: nothing ran at the edited value, so the item
  // withdraws it rather than show the recorded one beside a changed setup.
  const withdrawn = reading !== null && invalidated.has(reading.candidate_id);
  const rulerNote = ownNote && reading?.comparable === false;
  const level = reading && {
    value: withdrawn ? "?" : fmtMetricValue(unit, reading.value),
    band: withdrawn
      ? "? · ? cells"
      : `${fmtMetricInterval(unit, reading.ci_lo, reading.ci_hi)} · ${reading.n_cells} cell${
          reading.n_cells === 1 ? "" : "s"
        }`,
  };

  const lead = benchLead(headline);

  const idRow = (
    <div className="cmp-channel-row">
      <div className="cmp-channel-id">
        <span className="cmp-channel-name">
          {own && (
            <span className="cmp-swatch" style={{ background: own.ink }} aria-hidden="true" />
          )}
          {headline
            ? headline.optimizer
            : reading && reading.kind !== "campaign"
              ? reading.label
              : campaignName}
        </span>
        <span className="cmp-channel-sub" title={subject}>
          {reading ? `${KIND_WORD[reading.kind]} · ${reading.campaign_id}` : "nothing measured"}
        </span>
        {headline && <HeadlineBadges row={headline} uncontrolledNote={uncontrolledNote} />}
      </div>
      {headline ? (
        <>
          <Metric label="bench" {...lead.score} />
          <Metric label="lift over origin" {...lead.lift} />
        </>
      ) : (
        level && (
          <div className={cx("cmp-channel-lead", withdrawn && "cmp-channel-unknown")}>
            <Metric label={axis} value={level.value} band={level.band} />
          </div>
        )
      )}
      <button
        type="button"
        className="cmp-link cmp-channel-close"
        aria-label="Remove this channel from the comparison"
        onClick={() => onRemove(subject)}
      >
        ✕
      </button>
      {headline && <CostFacts row={headline} />}
    </div>
  );
  const notes =
    reading === null ? (
      <p className="l4-note">
        Nothing measured at this address — it is named in the read&rsquo;s{" "}
        <code>unread_subjects</code> rather than counted as a low number. Open the lineage below
        and pick a point that has run.
      </p>
    ) : (
      <>
        {withdrawn && (
          <p className="l4-warn">
            ✗ A setting was changed at or above the point this channel reads, and nothing ran
            under it. Every number here is unknown until it is measured — restore it below, or
            steer &amp; fork from that searchpoint to actually run it.
          </p>
        )}
        {/* The sentence is served (`comparable_note`); never word it here. It qualifies the level,
            so it sits wherever the level does — here only where the level leads. */}
        {rulerNote && !headline && <p className="l4-warn">{reading.comparable_note}</p>}
        {/* A fact about the RUN, not the author — a loop-authored arm carries it too. */}
        {reading.human_intervened && (
          <p className="l4-warn">
            An operator intervened mid-run on this cycle, so it is no longer purely reproducible.
          </p>
        )}
      </>
    );
  const detailLabel = reading === null ? "Lineage" : "Search reading, lineage and configuration";
  const body = (
    <>
      {detailOpen && reading === null && (
        <ChannelMap
          root={root}
          index={index}
          loaded={loaded}
          failed={failed}
          rootPath={rootPath}
          reading={reading}
          points={points}
          own={own}
          subject={subject}
          selected={selected}
          setSelected={setSelected}
          invalidated={invalidated}
          onReplace={onReplace}
          onAdd={onAdd}
          hasSubject={hasSubject}
        />
      )}
      {detailOpen && reading !== null && level && (
        <>
          {/* A SELECTOR, not a navigator. An unavailable head is dropped, not disabled; a map click
              lands on "Picked" because the lit segment derives from the selection. */}
          {!column && head.options.length > 1 && (
            <SegmentedControl
              options={head.options}
              value={head.value}
              onChange={(v) => setSelected(head.nodeFor(v))}
              ariaLabel="Which searchpoint of this branch is highlighted"
            />
          )}
          <dl className="cmp-channel-facts">
            {headline && (
              <>
                <div>
                  <dt>{axis} on search rows</dt>
                  <dd>
                    {level.value} {level.band}
                  </dd>
                </div>
                <div>
                  <dt>arm</dt>
                  <dd>{headline.arm ? headline.arm.arm_key : "none"}</dd>
                </div>
                <div>
                  <dt>treatment</dt>
                  <dd title={headline.treatment_digest ?? undefined}>
                    {headline.treatment_digest?.slice(0, 8) ?? "—"}
                  </dd>
                </div>
                <div>
                  <dt>bench reads</dt>
                  <dd>{headline.bench_reads ?? "—"}</dd>
                </div>
              </>
            )}
            {/* Served `spend_to_round`, indexed by the looked-at round. A pick on another lane falls
                back to the cycle's roll-up, and says which it shows. */}
            <div>
              <dt>{spentTo !== null ? `spent to ${selected?.label}` : "branch spend"}</dt>
              <dd>
                {spentTo !== null
                  ? fmtUsd(spentTo)
                  : reading.cycle_spend_usd != null
                    ? fmtUsd(reading.cycle_spend_usd)
                    : "—"}
              </dd>
            </div>
            <div>
              <dt>dataset</dt>
              <dd title={reading.dataset_name}>{reading.dataset_name || "—"}</dd>
            </div>
            <div>
              <dt>campaign</dt>
              <dd title={reading.campaign_id}>{shortId(reading.campaign_id)}</dd>
            </div>
            {/* An inner campaign id alone says nothing about which run opened its sandbox. */}
            {reading.inside.length > 0 && (
              <div>
                <dt>seed of</dt>
                <dd title={reading.inside.map((h) => h.campaign_id).join(" → ")}>
                  {shortId(reading.inside[reading.inside.length - 1]?.campaign_id ?? "")}
                </dd>
              </div>
            )}
            <div>
              <dt>branch</dt>
              <dd title={reading.cycle_id}>{shortId(reading.cycle_id)}</dd>
            </div>
            <div>
              <dt>reads at</dt>
              <dd title={reading.candidate_id}>
                {head.own?.label ?? reading.label} · round {reading.round}
              </dd>
            </div>
            {/* How deep the BRANCH went, not how deep the point sits. */}
            <div>
              <dt>rounds on branch</dt>
              <dd>{reading.cycle_rounds_scored}</dd>
            </div>
            {/* Served `authorship`: the arm groups by optimizer CONFIG, which forks share, so this is
                the only row separating a human's prompt from the optimizer's. */}
            <div>
              <dt>authored by</dt>
              <dd title={reading.authorship}>{reading.authorship || "—"}</dd>
            </div>
            {/* Absent and zero differ: `—` is no report for this point, `0` is every cell earned
                here, and a rewind fork's inherited rows are neither. */}
            <div>
              <dt>replayed cells</dt>
              <dd>{reading.cached_samples ?? "—"}</dd>
            </div>
          </dl>
          {rulerNote && headline && <p className="l4-warn">{reading.comparable_note}</p>}

          <p className="cmp-channel-lineage">
            <button
              type="button"
              className="cmp-link"
              aria-expanded={mapOpen}
              onClick={() => setMapOpen((v) => !v)}
            >
              {mapOpen ? "▾" : "▸"} Lineage
            </button>
          </p>
          {mapOpen && (
            <ChannelMap
              root={root}
              index={index}
              loaded={loaded}
              failed={failed}
              rootPath={rootPath}
              reading={reading}
              points={points}
              own={own}
              subject={subject}
              selected={selected}
              setSelected={setSelected}
              invalidated={invalidated}
              onReplace={onReplace}
              onAdd={onAdd}
              hasSubject={hasSubject}
            />
          )}

          {/* Follows the pick and stays OPEN across it. The restore and copy controls are SIBLINGS
              of `<summary>`: inside it they are invalid markup and a press toggles the fold. */}
          <div className="cmp-channel-setup-row">
            <details className="cmp-channel-setup" open={setupOpen}>
              <summary
                onClick={(e) => {
                  e.preventDefault();
                  setSetupOpen((v) => !v);
                }}
              >
                <span>
                  {selected
                    ? `${selected.label} · round ${selected.round ?? 0}`
                    : "This searchpoint"}
                </span>
                {!pickedIsOwn && (
                  <span className="l4-dim">
                    {" "}
                    — this channel reads at {head.own?.label ?? reading.label}
                  </span>
                )}
              </summary>
              <SearchpointDrillIn
                row={pickedRow}
                cfg={pickedCfg}
                // Served for the point this channel READS, so a pick elsewhere shows none.
                stats={pickedIsOwn && <CellMeans means={reading.cell_means} />}
                measurements={
                  pickedPath &&
                  docId && (
                    <MeasurementsPane
                      preset={{
                        path: pickedPath,
                        datasetName: reading.dataset_name,
                        candidateId: docId,
                        scope: "cycle",
                        groupBy: "none",
                      }}
                    />
                  )
                }
                arms={pickedArms}
                schema={pipeline?.node_config_schema ?? null}
                schemaStatus={pipelineStatus}
                outputSchema={pipeline?.node_output_schema ?? null}
                // The editor drops its draft whenever the seed changes, so seeding with the scenario
                // written back makes a restore refill the inputs.
                overlay={pickedSeed}
                pending={
                  docLoading
                    ? "Reading this searchpoint's round document…"
                    : "No round document on disk for this point — a round still scoring has not written one yet, and this tab streams no cycle to read it from."
                }
                onOverlay={(next) =>
                  onEdits(withOverlay(edits, pickedKey, next, pickedCfg?.config ?? {}))
                }
                actions={
                  selected &&
                  pickedPath && (
                    <>
                    <VerifyAction
                      candidate={selectedNodeOf(selected, pickedPath.at(-1)?.cycleId ?? "")}
                      path={pickedPath}
                    />
                    <SteerForkAction
                      candidate={selectedNodeOf(selected, pickedPath.at(-1)?.cycleId ?? "")}
                      path={pickedPath}
                      // Exactly one cycle streams — whichever the dashboard is parked on — so no stream here.
                      dash={null}
                      schema={pipeline?.node_config_schema ?? null}
                      schemaStatus={pipelineStatus}
                      isSingleNode={!!pipeline?.is_single_node}
                      outputSchema={pipeline?.node_output_schema ?? null}
                      parentIsLive={
                        index.get(encodeCyclePath(pickedPath))?.course?.run_phase === "running"
                      }
                    />
                    </>
                  )
                }
              />
            </details>
            <ChannelRestore edits={edits} subjectKey={pickedKey} onEdits={onEdits} />
            {/* Same builder as the dashboard's Scoring inspector, so a point pasted from either host
                compares against the other. */}
            <CopyButton
              choices={searchpointCopyChoices({
                cfg: pickedCfg,
                row: pickedRow,
                samples: pickedSamples,
                arms: pickedArms,
              })}
              title="Copy this searchpoint"
            />
          </div>
        </>
      )}
    </>
  );

  if (!column || !headline) {
    return (
      <li className="cmp-channel">
        {idRow}
        {notes}
        <details className="cmp-channel-detail" open={detailOpen}>
          <summary
            onClick={(e) => {
              e.preventDefault();
              setDetailOpen((v) => !v);
            }}
          >
            {detailLabel}
          </summary>
          {body}
        </details>
      </li>
    );
  }

  // The sidebar's own reading of the campaign, live off the registry poll, less its bench and spend
  // stats: the rows below read both off the head-to-head row, the bench under its one grader.
  const summary = run && campaignCard(run, root, campaignStatus(run).word);
  const close = (
    <button
      type="button"
      className="cmp-link cmp-channel-close"
      aria-label="Remove this campaign from the comparison"
      onClick={() => onRemove(subject)}
    >
      ✕
    </button>
  );
  const cell = (row: ColumnRow): ReactNode => {
    switch (row) {
      case "head":
        return (
          <div className="cmp-col-head">
            {own && (
              <span className="cmp-swatch" style={{ background: own.ink }} aria-hidden="true" />
            )}
            {summary ? (
              <SummaryBlock
                dense
                facts={{
                  ...summary,
                  tags: [shortId(headline.campaign_id), ...(summary.tags ?? [])],
                  stats: summary.stats.filter(
                    (s) => s.label !== BENCH_STAT_LABEL && s.label !== SPEND_STAT_LABEL,
                  ),
                }}
                actions={close}
              />
            ) : (
              <span className="cmp-channel-name">
                {campaignName}
                {close}
              </span>
            )}
          </div>
        );
      case "optimizer":
        return <span className="cmp-col-optimizer">{headline.optimizer}</span>;
      case "bench":
        return <Metric {...lead.score} />;
      case "lift":
        return <Metric {...lead.lift} />;
      case "per_usd":
        return fmtSigned(headline.lift_per_incurred_usd, 2);
      case "spent":
        return <SpentReading metered={headline.spend_metered} />;
      case "worked":
        return headline.worked_s === null ? "—" : fmtDuration(headline.worked_s);
      case "standing":
        return <HeadlineBadges row={headline} uncontrolledNote={uncontrolledNote} />;
      case "reads":
        return (
          <div className="cmp-col-reads">
            {head.options.length > 1 && (
              <SegmentedControl
                options={head.options}
                value={head.value}
                onChange={(v) => setSelected(head.nodeFor(v))}
                ariaLabel="Which searchpoint this column reads"
              />
            )}
            <span className="cmp-metric-band">
              {selected ? `${selected.label} · round ${selected.round ?? 0}` : "—"}
            </span>
          </div>
        );
      case "line":
        return <MainLine steps={line} />;
      case "more":
        return (
          <button
            type="button"
            className="cmp-link"
            aria-expanded={detailOpen}
            onClick={() => setDetailOpen((v) => !v)}
          >
            {detailOpen ? "▾" : "▸"} Reading, lineage, config
          </button>
        );
    }
  };
  return (
    <>
      <div className="cmp-col" role="group" aria-label={`${headline.optimizer} · ${campaignName}`}>
        {column.rows.map((r) => (
          <div key={r} className={cx("cmp-cell", `is-${r}`)}>
            {cell(r)}
          </div>
        ))}
      </div>
      {detailOpen &&
        column.panelHost &&
        createPortal(
          <section
            className="cmp-channel"
            style={{ order: column.order }}
            aria-label={`${headline.optimizer} · ${campaignName} in detail`}
          >
            <span className="cmp-channel-name">
              {own && (
                <span className="cmp-swatch" style={{ background: own.ink }} aria-hidden="true" />
              )}
              {headline.optimizer} · {campaignName}
            </span>
            {notes}
            {body}
          </section>,
          column.panelHost,
        )}
    </>
  );
}

// The three heads as NODES — the picker selects, it does not navigate. Winner is the last crowned
// candidate; most recent is the last one the highest round minted, crowned or not.
function useChannelHead(
  index: ReturnType<typeof indexLineage>,
  reading: SubjectReading | null,
  selected: LineageNode | null,
) {
  return useMemo(() => {
    const courseKey = reading ? encodeCyclePath(readingPath(reading)) : null;
    const candidates = (courseKey && index.get(courseKey)?.candidates) || [];
    // Tree order, so the LAST node at the max round is the most recent (no re-sort). `is_selected`
    // alone says nothing on a round still scoring, so the crown walk reads `election_held`.
    let newest: LineageNode | null = null;
    let crowned: LineageNode | null = null;
    for (const c of candidates) {
      if (c.round == null) continue;
      if (newest === null || c.round >= (newest.round ?? -1)) newest = c;
      if (c.is_selected && c.election_held && (crowned === null || c.round >= (crowned.round ?? -1))) {
        crowned = c;
      }
    }
    const own = candidates.find((c) => c.id === reading?.candidate_id) ?? null;
    // On a branch with no crown, the point the server resolved the course to.
    const winner = crowned ?? own;
    const latest = newest;
    // Same point only when the COURSE agrees too: a fork-contributed attempt keeps its own id.
    const sameAs = (a: LineageNode | null, b: LineageNode | null) =>
      !!a && !!b && a.id === b.id && encodeCyclePath(pathOf(a)) === encodeCyclePath(pathOf(b));
    const isWinner = sameAs(selected, winner);
    const isLatest = sameAs(selected, latest);
    const byValue: Record<string, LineageNode | null> = {
      winner,
      latest,
      picked: isWinner || isLatest ? null : selected,
    };
    const options = [
      winner && {
        value: "winner",
        label: "Winner",
        title: "The last searchpoint an election on this branch crowned",
      },
      latest &&
        !sameAs(latest, winner) && {
          value: "latest",
          label: "Most recent",
          title: "The newest searchpoint this branch minted, crowned or not",
        },
      selected &&
        !isWinner &&
        !isLatest && {
          value: "picked",
          label: "Picked",
          title: "The searchpoint clicked on the map below",
        },
    ].filter(Boolean) as { value: string; label: string; title: string }[];
    return {
      options,
      own,
      value: isWinner ? "winner" : isLatest ? "latest" : "picked",
      nodeFor: (v: string) => byValue[v] ?? null,
    };
  }, [index, reading, selected]);
}

// The dashboard's cladogram, mapping the CAMPAIGN, never the reading — an unread channel needs a map most.
function ChannelMap({
  root,
  index,
  loaded,
  failed,
  rootPath,
  reading,
  points,
  own,
  subject,
  selected,
  setSelected,
  invalidated,
  onReplace,
  onAdd,
  hasSubject,
}: {
  // The CARD's: the head picker needs them before this opens; a second subscription is a second reading.
  root: LineageNode | null;
  index: LineageIndex;
  loaded: boolean;
  failed: boolean;
  selected: LineageNode | null;
  setSelected: (next: LineageNode | null) => void;
  // Edited points plus their descendants: their numbers are withdrawn, not dimmed — nothing ran there.
  invalidated: ReadonlySet<string>;
  // The ROOT course: the server's recursion reaches every fork and L4 sandbox, and the rooting matches
  // `LineageProvider`, so a card on the viewed campaign rides the tree already on screen.
  rootPath: CyclePath;
  reading: SubjectReading | null;
  points: readonly CladogramChannel[];
  // Where the drawing is CUT.
  own: CladogramChannel | null;
  subject: string;
  onReplace: (from: string, to: string) => void;
  onAdd: (channel: CompareChannel) => void;
  hasSubject: (subject: string) => boolean;
}) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  // Cut at this card's point by default; lifting it lets a channel move FORWARD past its own round.
  const [whole, setWhole] = useState(false);

  const courses = useMemo(() => (root ? walkCourses(root) : []), [root]);
  // Accuracy, never the composite: the card's value already rides the selected metric.
  const { valueByKey, thetaByKey } = useMemo(() => nodeOverlays(courses, false), [courses]);

  // The served `inside` chain makes this join at any depth.
  const viewedKey = useMemo(
    () => (reading ? encodeCyclePath(readingPath(reading)) : null),
    [reading],
  );
  // One lane per channel this tree holds; only the tree can name a lane (`nodeKeyOf`), and a card
  // whose channels are all elsewhere opens on the ROOT's lane.
  const laneKeys = useMemo(() => {
    const keys = [
      ...new Set(
        points.flatMap((p) => {
          const course = index.get(p.coursePathKey)?.course;
          return course ? [nodeKeyOf(course)] : [];
        }),
      ),
    ];
    if (keys.length > 0) return keys;
    return root ? [nodeKeyOf(root)] : [];
  }, [root, points, index]);
  // Compared as a STRING: `points` is rebuilt every evidence fetch, and re-seeding would drop opened lanes.
  const seed = laneKeys.join("~");
  const [seeded, setSeeded] = useState<string | null>(null);
  if (seed && seed !== seeded) {
    setSeeded(seed);
    setExpanded(new Set(laneKeys));
    // The selection is NOT re-seeded here — it belongs to the card; clearing it would leave the
    // picker lit on nothing.
  }

  const onLaneActivate = useCallback((key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  }, []);

  const ctx = useMemo<CladogramCtx>(
    () => ({
      viewedKey,
      channels: points,
      clip: whole ? null : own,
      invalidated,
      // Never moves the CUT, which stays on this card's own point.
      isPicked: (n: RoundNodePos) =>
        !!selected &&
        n.candidateId === selected.id &&
        n.coursePathKey === encodeCyclePath(pathOf(selected)),
      // The SERVED node rides the position: a lane lookup finds nothing for a fork-contributed
      // attempt, drawn on the parent's lane but indexed under the fork's.
      onPickCandidate: (n: RoundNodePos) => setSelected(n.node === selected ? null : n.node),
    }),
    [viewedKey, selected, setSelected, points, own, whole, invalidated],
  );

  if (rootPath.length === 0) {
    return <p className="l4-empty">This campaign has no branch in the registry to map.</p>;
  }
  if (failed) return <p className="l4-warn">This campaign&rsquo;s lineage could not be read.</p>;
  if (!root) {
    return <p className="l4-empty">{loaded ? "No rounds on disk yet." : "Reading the lineage…"}</p>;
  }

  return (
    <div className="cmp-channel-map">
      {own && (
        <p className="cmp-map-scope">
          <span className="l4-dim">
            {whole
              ? "The whole campaign — everything after this point too."
              : "How this point came to be — the family up to it, and no further."}
          </span>
          <button type="button" className="cmp-link" onClick={() => setWhole((v) => !v)}>
            {whole ? "Cut to this point" : "Show the whole campaign"}
          </button>
        </p>
      )}
      {selected && (
        <ArmedActions
          armed={selected}
          subject={subject}
          onReplace={onReplace}
          onAdd={onAdd}
          hasSubject={hasSubject}
        />
      )}
      {/* DENSE always: a card is half-width by construction; labels ride each node's `<title>`. */}
      <Forest
        tree={root}
        valueByKey={valueByKey}
        thetaByKey={thetaByKey}
        metric="accuracy"
        expanded={expanded}
        onLaneActivate={onLaneActivate}
        ctx={ctx}
        d={DENSE}
      />
    </div>
  );
}

// Both verbs mint the SAME address; they differ only in whether this channel moves or a new one joins.
function ArmedActions({
  armed,
  subject,
  onReplace,
  onAdd,
  hasSubject,
}: {
  armed: LineageNode;
  subject: string;
  onReplace: (from: string, to: string) => void;
  onAdd: (channel: CompareChannel) => void;
  hasSubject: (subject: string) => boolean;
}) {
  const path = pathOf(armed);
  const leaf = path.at(-1);
  const top = path.at(0);
  if (!leaf || !top) return null;
  // Any depth: the hops above the leaf are the sandbox chain. The ADDRESS is the leaf's; the channel's
  // CAMPAIGN is the top hop's.
  const next = candidateSubject(path, armed.id);
  // Silent on this channel's OWN point, where the card seeds the selection.
  if (next === subject) return null;
  const already = hasSubject(next);
  return (
    <p className="cmp-armed">
      <strong>{armed.label}</strong>
      <span className="l4-dim">
        {" "}
        R{armed.round ?? 0} · {armed.accuracy === null ? "—" : fmtPct0(armed.accuracy)}
      </span>
      {/* No accuracy on the tree, so the read will find nothing — said, not blocked: an arm still
          scoring is worth putting on the board to watch fill in. */}
      {armed.accuracy === null && (
        <span className="l4-dim">nothing scored here yet — it reads as unmeasured</span>
      )}
      <button
        type="button"
        className="cmp-button"
        disabled={next === subject}
        onClick={() => onReplace(subject, next)}
      >
        Move this channel here
      </button>
      <button
        type="button"
        className="cmp-button"
        disabled={already}
        onClick={() => onAdd({ rootCampaignId: top.campaignId, subject: next })}
      >
        {already ? "Already a channel" : "Compare — add as a channel"}
      </button>
    </p>
  );
}
