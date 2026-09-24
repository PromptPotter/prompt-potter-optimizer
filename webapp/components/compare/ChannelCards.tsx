"use client";
// One card per Compare channel: its served reading, a config drill-in, and the campaign's cladogram as a map.
// Every number is served (`SubjectReading`); a pick on the map moves the highlight, never the channel or the cut.

import { useCallback, useMemo, useState } from "react";
import type {
  CampaignSummary,
  CycleListEntry,
  Evidence,
  LineageNode,
  SubjectReading,
} from "@/lib/api";
import { fetchCampaignPipeline } from "@/lib/api";
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
  candidateObserveConfig,
  descendantsOf,
  docCandidateId,
  historicalSamplesFor,
  indexLineage,
  nodeKeyOf,
  nodeOverlays,
  pathOf,
  pipelineReadStatus,
  scoreboardRow,
  searchpointCopyChoices,
  selectedCandidateOf,
  walkCourses,
  type LineageIndex,
} from "@/lib/derivations";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { useRoundFile } from "@/lib/hooks/useRoundFile";
import { useLineageTree } from "@/lib/lineage";
import { fmtMetricInterval, fmtMetricValue, fmtPct0, fmtUsd, shortId } from "@/lib/format";
import { encodeCyclePath, rootCycleId, type CyclePath } from "@/lib/ids";
import { seriesVar } from "@/lib/theme";
import { useWorkspace } from "@/lib/workspace";
import {
  ChannelRestore,
  editsFor,
  pointKeyOf,
  withOverlay,
  type ScenarioEdits,
} from "./config-edit";
import { CopyButton, SegmentedControl } from "@/components/ui";

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
  onReplace: (from: string, to: string) => void;
  onAdd: (channel: CompareChannel) => void;
  hasSubject: (subject: string) => boolean;
  onRemove: (subject: string) => void;
}) {
  const byKey = useMemo(
    () => new Map(evidence.subjects.map((s) => [s.key, s])),
    [evidence.subjects],
  );
  const points = useMemo(() => channelPoints(evidence.subjects), [evidence.subjects]);
  return (
    <div className="cmp-channels">
      {channels.map((channel) => {
        const reading = byKey.get(channel.subject) ?? null;
        return (
          <ChannelCard
            key={channel.subject}
            channel={channel}
            reading={reading}
            points={points}
            // Null for an unread channel: it plots no series, so any ink here would be another channel's.
            own={reading ? points[evidence.subjects.indexOf(reading)] ?? null : null}
            edits={edits}
            onEdits={onEdits}
            unit={evidence.metric.spec.unit}
            axis={evidence.metric.spec.axis_label}
            onReplace={onReplace}
            onAdd={onAdd}
            hasSubject={hasSubject}
            onRemove={onRemove}
          />
        );
      })}
    </div>
  );
}

function ChannelCard({
  channel,
  reading,
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
}: {
  channel: CompareChannel;
  reading: SubjectReading | null;
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
}) {
  const subject = channel.subject;
  const { campaigns, cycles } = useWorkspace();
  const campaign = campaigns.find(
    (c: CampaignSummary) => c.campaign_id === channel.rootCampaignId,
  );
  const campaignName = campaign?.label || shortId(channel.rootCampaignId);
  // The TOP-LEVEL campaign (the registry lists no inner one), read off the registry — never parsed from
  // the subject: `lib/api/reads.ts` is the one place the browser spells the address grammar.
  const anyCycle = cycles.find((c: CycleListEntry) => c.campaign_id === channel.rootCampaignId);
  const rootPath = useMemo<CyclePath>(
    () =>
      anyCycle
        ? [{ campaignId: channel.rootCampaignId, cycleId: rootCycleId(anyCycle.cycle_id) }]
        : [],
    [anyCycle, channel.rootCampaignId],
  );

  // One tree subscription per card: the head picker needs the genealogy before the map ever opens.
  const { root, loaded, failed } = useLineageTree(rootPath, rootPath.length > 0);
  const index = useMemo(() => indexLineage(root), [root]);
  // One slot, two writers (head picker, map click). It moves only the highlight; re-pointing the
  // channel is the explicit verb in `ArmedActions`.
  const [selected, setSelected] = useState<LineageNode | null>(null);
  const head = useChannelHead(index, reading, selected);
  // Render-phase seed: a `useEffect` would paint one frame of the previous channel's pick.
  const [seededFor, setSeededFor] = useState<string | null>(null);
  if (head.own && seededFor !== subject) {
    setSeededFor(subject);
    setSelected(head.own);
  }
  const [mapOpen, setMapOpen] = useState(false);
  // Must NOT close when the pick moves — that is the moment the operator asked to see something.
  const [setupOpen, setSetupOpen] = useState(true);

  // No live snapshot: exactly one cycle streams (`webapp/CLAUDE.md` § Polling shape), so a round
  // still scoring has nothing to read here.
  const pickedPath = useMemo(() => (selected ? pathOf(selected) : null), [selected]);
  const { doc, loading: docLoading } = useRoundFile(pickedPath, selected?.round ?? null);
  // Addressed as the point, never by dataset name: one `pipeline.yaml` is shared by every campaign on the slug.
  const at = useMemo(
    () => (pickedPath && selected ? candidateSubject(pickedPath, selected.id) : ""),
    [pickedPath, selected],
  );
  const pickedCampaign = pickedPath?.at(-1)?.campaignId ?? "";
  const pipelineRead = useRead(
    pickedCampaign && at
      ? {
          key: `${pickedCampaign}\x1f${at}`,
          fetch: (s) => fetchCampaignPipeline(pickedCampaign, at, s),
        }
      : null,
    { surface: "campaign-pipeline" },
  );
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
  // The DOCUMENT's own id, via the served join key: a tree id differs after a resume re-mints C0. Key on
  // `course_label`, never `label` — a fork-contributed attempt keeps its minting course's label in the doc.
  const docId = selected ? docCandidateId(doc, selected.course_label) : null;
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
  // the server's (`webapp/CLAUDE.md` § Addressing).
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

  return (
    <section className="cmp-channel">
      <header className="cmp-channel-head">
        {own && (
          <span className="cmp-swatch" style={{ background: own.ink }} aria-hidden="true" />
        )}
        <span className="cmp-channel-name" title={subject}>
          {reading
            ? reading.kind === "campaign"
              ? shortId(reading.label)
              : reading.label
            : campaignName}
        </span>
        <span className="l4-dim">{reading ? KIND_WORD[reading.kind] : "nothing measured"}</span>
        <button
          type="button"
          className="cmp-link cmp-channel-close"
          aria-label={`Remove this channel from the comparison`}
          onClick={() => onRemove(subject)}
        >
          ✕
        </button>
      </header>

      {reading === null ? (
        <p className="l4-note">
          Nothing measured at this address — it is named in the read&rsquo;s{" "}
          <code>unread_subjects</code> rather than counted as a low number. Open the lineage below
          and pick a point that has run.
        </p>
      ) : (
        <>
          {/* An edit removes the ground under this number: nothing ran at the edited value, so the
              card withdraws the level rather than show the recorded one beside a changed setup. */}
          {invalidated.has(reading.candidate_id) ? (
            <>
              <p className="cmp-channel-value cmp-channel-unknown">
                <span className="cmp-channel-num">?</span>
                <span className="l4-subtle"> ? · ? cells</span>
              </p>
              <p className="l4-warn">
                ✗ A setting was changed at or above the point this channel reads, and nothing ran
                under it. Every number here is unknown until it is measured — restore it below, or
                steer &amp; fork from that searchpoint to actually run it.
              </p>
            </>
          ) : (
            <p className="cmp-channel-value">
              <span className="cmp-channel-num">{fmtMetricValue(unit, reading.value)}</span>
              <span className="l4-subtle">
                {" "}
                {fmtMetricInterval(unit, reading.ci_lo, reading.ci_hi)} · {reading.n_cells} cell
                {reading.n_cells === 1 ? "" : "s"}
              </span>
            </p>
          )}
          <p className="l4-subtle">{axis}</p>
          {/* A SELECTOR, not a navigator. An unavailable head is dropped, not disabled; a map click
              lands on "Picked" because the lit segment derives from the selection. */}
          {head.options.length > 1 && (
            <SegmentedControl
              options={head.options}
              value={head.value}
              onChange={(v) => setSelected(head.nodeFor(v))}
              ariaLabel="Which searchpoint of this branch is highlighted"
            />
          )}
          <dl className="cmp-channel-facts">
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
                the only row separating a human's prompt from L1's. */}
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
          {/* The sentence is served (`comparable_note`); never word it here. */}
          {reading.comparable === false && (
            <p className="l4-warn">{reading.comparable_note}</p>
          )}
          {/* A fact about the RUN, not the author — a loop-authored arm carries it too. */}
          {reading.human_intervened && (
            <p className="l4-warn">
              An operator intervened mid-run on this cycle, so it is no longer purely
              reproducible.
            </p>
          )}

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
                      candidate={selectedCandidateOf(selected, pickedPath.at(-1)?.cycleId ?? "")}
                      path={pickedPath}
                    />
                    <SteerForkAction
                      candidate={selectedCandidateOf(selected, pickedPath.at(-1)?.cycleId ?? "")}
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
    </section>
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
    // Tree order, so the LAST node at the max round is the most recent (no re-sort). `is_winner`
    // alone says nothing on a round still scoring, so the crown walk reads `election_held`.
    let newest: LineageNode | null = null;
    let crowned: LineageNode | null = null;
    for (const c of candidates) {
      if (c.round == null) continue;
      if (newest === null || c.round >= (newest.round ?? -1)) newest = c;
      if (c.is_winner && c.election_held && (crowned === null || c.round >= (crowned.round ?? -1))) {
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
