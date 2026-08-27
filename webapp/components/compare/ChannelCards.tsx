"use client";
// One card per channel, side by side — the Compare tab's primary surface.
//
// A card LANDS on its campaign's winner and says what that point is worth. Behind a fold it
// carries WHAT that searchpoint is, editable: an edit invalidates rather than re-projects, so the
// card's own numbers go to `?` (`config-edit.tsx`).
//
// Also behind a toggle is the CLADOGRAM the dashboard draws (`candidates/Forest`), used here as an
// interactive map: click any searchpoint and the card offers the two things worth doing with it —
// move this channel there, or put it on the board as a channel of its own. That second verb is the
// whole point of walking historical rounds: the winner stays where it is, and R3.2 joins it.
//
// **Selecting is not navigating, and the head picker is a SELECTOR.** Winner / Most recent /
// Picked move the HIGHLIGHT on that map and nothing else — the drawing's CUT stays on the
// channel's own point, so walking history never re-cuts the picture under the cursor that clicked
// it. The two verbs above are the only things that move a channel, and they are presses.
//
// The cladogram is reused verbatim rather than re-drawn. Everything surface-specific arrives
// through `CladogramCtx`, so the compare flavour differs from the dashboard's only in what a
// click DOES — and in the INK: every channel of the comparison is marked on every card's tree in
// its own colour, so the drawing answers "where do these two sit relative to each other" without
// anyone having to hold two pictures in their head. A second lineage renderer here would be a
// second answer to "what descends from what", and the tree is the one genealogy this app reads.
//
// Every number on the card is served (`SubjectReading`); nothing here computes a level, an
// interval or a verdict.

import { useCallback, useMemo, useState } from "react";
import type {
  CampaignSummary,
  CycleListEntry,
  Evidence,
  LineageNode,
  SubjectReading,
} from "@/lib/api";
import { fetchDatasetPipeline } from "@/lib/api";
import { candidateSubject, readingPath } from "@/lib/api/reads";
import { SteerForkAction } from "@/components/shell/searchpoint/SteerForkAction";
import {
  Forest,
  type CladogramChannel,
  type CladogramCtx,
} from "@/components/candidates/Forest";
import { DENSE, type RoundNodePos } from "@/components/candidates/forest-layout";
import { SearchpointDrillIn } from "@/components/shell/searchpoint/SearchpointDrillIn";
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
  scoreboardRow,
  selectedCandidateOf,
  walkCourses,
  type LineageIndex,
} from "@/lib/derivations";
import { useFetch } from "@/lib/hooks/useFetch";
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
import { SegmentedControl } from "@/components/ui";

// Every address at or above one, leaf first — the walk a "nearest enclosing X" question takes.
function prefixes(path: CyclePath): CyclePath[] {
  return path.map((_, i) => path.slice(0, path.length - i));
}

const KIND_WORD: Record<SubjectReading["kind"], string> = {
  campaign: "origin",
  course: "branch head",
  candidate: "searchpoint",
};

// Where each channel LANDS in the served genealogy, and what colour it owns there. The three ids
// name the leaf and `inside` is the sandbox chain above it, so the two halves together are the
// same address a tree node publishes as `coursePathKey` + `candidateId` — which is what lets an
// L4 seed's point be found on the outer campaign's tree at all.
function channelPoints(subjects: readonly SubjectReading[]): CladogramChannel[] {
  return subjects.map((s, i) => ({
    coursePathKey: encodeCyclePath(readingPath(s)),
    candidateId: s.candidate_id,
    // The Nth series ink, by the SERVED order — the same index the bars, the legend and the
    // pairwise table read, so one channel is one colour everywhere it appears.
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
  // Which channels the operator has edited the CONFIGURATION of. Nothing ran under an edited
  // value, so this card stops claiming a level for that channel rather than showing the recorded
  // one under a changed setup — see `config-edit.tsx`.
  edits: ScenarioEdits;
  onEdits: (next: ScenarioEdits) => void;
  // The channels ASKED for, in selection order. Read off the request rather than the response
  // so a channel that answered nothing still gets a card saying so — dropping it silently is
  // how a three-channel comparison reads as a two-channel one. And each one names its CAMPAIGN
  // beside the address, which is what lets an unread channel still draw a map to escape by.
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
  // Every channel's position on the genealogy, computed ONCE for the whole board and handed to
  // every card: each tree marks all of them, not just its own. That is the comparison — one
  // drawing, two inked points — and a per-card list would mark each card only where it already
  // stands, which is the picture the operator can already see in the header.
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
            // This card's OWN point: the ink of its swatch, and the cut its map is made at. An
            // unread channel has neither — no series is drawn for it, so a colour here would be
            // some OTHER channel's ink on a card that plots nothing.
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
  // A card names its CAMPAIGN when the point it addresses answered nothing. It used to render
  // an em-dash, so two unread cards were indistinguishable from each other and from a card
  // still loading — and the campaign is the one thing a channel always knows.
  const campaign = campaigns.find(
    (c: CampaignSummary) => c.campaign_id === channel.rootCampaignId,
  );
  const campaignName = campaign?.label || shortId(channel.rootCampaignId);
  // Any branch of this campaign names its root, and the tree is rooted there whatever the card
  // reads on. Sourced from the registry rather than parsed back out of the subject: the address
  // grammar is the server's, and `lib/api/reads.ts` is the one place the browser spells it.
  // The channel's TOP-LEVEL campaign, which is why an L4 seed maps: the registry lists no inner
  // campaign, so rooting on the seed's own id found no branch and drew no tree at all.
  const anyCycle = cycles.find((c: CycleListEntry) => c.campaign_id === channel.rootCampaignId);
  const rootPath = useMemo<CyclePath>(
    () =>
      anyCycle
        ? [{ campaignId: channel.rootCampaignId, cycleId: rootCycleId(anyCycle.cycle_id) }]
        : [],
    [anyCycle, channel.rootCampaignId],
  );

  // ONE tree subscription per card, lifted out of the map: the head picker needs the genealogy to
  // name "most recent" before the map is ever opened, and two `useLineageTree` calls on one key
  // would be one fetch with two refcounts — but also two places deciding what the tree says.
  const { root, loaded, failed } = useLineageTree(rootPath, rootPath.length > 0);
  const index = useMemo(() => indexLineage(root), [root]);
  // WHICH searchpoint of this branch is highlighted. One slot, two writers — the head picker and
  // a click on the map — and it moves NOTHING but the highlight: re-pointing the channel is the
  // explicit verb below, and letting a pick do it silently re-cut the drawing under the cursor
  // that clicked it. The served NODE itself, not a projection of it: every scalar the drill-in
  // reads is already on the tree, so a middle shape would only be a place for them to go stale.
  const [selected, setSelected] = useState<LineageNode | null>(null);
  const head = useChannelHead(index, reading, selected);
  // Render-phase seed: the card opens with its OWN head marked, and re-seeds when the channel is
  // re-pointed elsewhere. A `useEffect` would paint one frame of the previous channel's pick.
  const [seededFor, setSeededFor] = useState<string | null>(null);
  if (head.own && seededFor !== subject) {
    setSeededFor(subject);
    setSelected(head.own);
  }
  // PER CARD, both of them. The map used to be one switch for the whole board on the argument
  // that two trees are only readable together; in practice one channel is usually the one being
  // walked, and a shared switch made every card grow and shrink to serve it.
  const [mapOpen, setMapOpen] = useState(false);
  // The drill-in stays where the operator left it — it must NOT close because the pick moved,
  // which is the one moment they have just asked to see something.
  const [setupOpen, setSetupOpen] = useState(true);

  // ── The highlighted point, read at its OWN address ────────────────────────────────────────
  // One round document, fetched for whatever is picked. No live snapshot: exactly one cycle
  // streams (`webapp/CLAUDE.md` § Polling shape) and it is whichever the dashboard is parked on,
  // which a Compare channel has no reason to be — so a round still scoring has nothing to read
  // and the drill-in says so, rather than claiming a liveness this tab cannot observe.
  const pickedPath = useMemo(() => (selected ? pathOf(selected) : null), [selected]);
  const { doc, loading: docLoading } = useRoundFile(pickedPath, selected?.round ?? null);
  // The node schema is per DATASET, and channels span datasets — the app-level connector view is
  // bound to whichever campaign the dashboard is viewing, so a card that trusted it would draw
  // another dataset's parameters, or (the usual case) none at all.
  //
  // Read off the nearest COURSE at or above the picked point, never off the candidate and never
  // off the channel: `dataset_name` is a course scalar and blank on a candidate, and an L4 inner
  // searchpoint runs a different dataset than the outer channel that opened its sandbox — so the
  // channel's name would seed the editor from the wrong pipeline entirely.
  const datasetName = useMemo(() => {
    for (const hops of pickedPath ? prefixes(pickedPath) : []) {
      const name = index.get(encodeCyclePath(hops))?.course?.dataset_name;
      if (name) return name;
    }
    return reading?.dataset_name ?? "";
  }, [index, pickedPath, reading?.dataset_name]);
  const { data: pipeline } = useFetch(
    datasetName ? (s: AbortSignal) => fetchDatasetPipeline(datasetName, s) : null,
    [datasetName],
  );
  // The point's own round, on its own course: how many arms stood, and where this one sat among
  // them. Both come off the tree, which is the only thing that knows a round's shape.
  const { pickedArms, pickedIdx } = useMemo(() => {
    if (!selected) return { pickedArms: null, pickedIdx: 0 };
    const sibs = (index.get(encodeCyclePath(pathOf(selected)))?.candidates ?? []).filter(
      (c) => c.round === selected.round,
    );
    return { pickedArms: sibs.length || null, pickedIdx: Math.max(sibs.indexOf(selected), 0) };
  }, [index, selected]);
  // THE DOCUMENT'S OWN ID for this point, resolved through the served join key and used for every
  // slice of that document from here down. A tree-sourced id is a DIFFERENT id after a resume —
  // the run re-mints C0's uuid while `round_0000.json`, written earlier, keeps the old one — so an
  // id carried across that boundary finds nothing and every panel blanks with no error.
  //
  // And the key is `course_label`, never `label`: a fork-contributed attempt is renumbered onto
  // this course's timeline while its document still speaks the label its MINTING course gave it.
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
  // WHICH searchpoint an edit here is about — the picked one, which is the channel's own until
  // the operator walks somewhere else.
  const pickedKey = useMemo(
    () => (selected ? candidateSubject(pathOf(selected), selected.id) || subject : subject),
    [selected, subject],
  );
  const pickedIsOwn = !!reading && pickedKey === pointKeyOf(reading);
  // What the branch had spent by the highlighted round. Served per round and INDEXED here, never
  // summed — the fold is one cycle's own ledger, so it answers only for a pick on that cycle's
  // lane; a pick on a fork's lane was billed to a file this read did not open, and the card says
  // "branch spend" rather than quietly showing the wrong branch's number.
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

  // ── What an edit took the ground out from under ───────────────────────────────────────────
  // Which points on THIS tree the operator has changed a setting on, and everything standing on
  // one. Found by asking each node for its own address rather than by parsing the edit keys back
  // apart: the subject grammar is the server's and `lib/api/reads.ts` is the one place the
  // browser spells it (`webapp/CLAUDE.md` § Addressing).
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
          {/* A config edit does not move this number — it removes the ground under it. Nothing
              ever ran at the edited value, so the level, its interval, its cell count and every
              pairwise test that used it describe a searchpoint this channel no longer names. The
              card says so instead of showing the recorded figure beside a changed setup, which is
              the one render that would let an operator read a measurement as an answer to a
              question it was never asked. */}
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
          {/* WHICH point of this branch is highlighted on the map — a SELECTOR, not a navigator.
              Three heads, and an unavailable one is DROPPED rather than disabled: a permanently
              dead segment is an affordance that lies. Clicking a node on the map lands on
              "Picked" by itself, because the lit segment is derived from what is selected rather
              than held beside it. */}
          {head.options.length > 1 && (
            <SegmentedControl
              options={head.options}
              value={head.value}
              onChange={(v) => setSelected(head.nodeFor(v))}
              ariaLabel="Which searchpoint of this branch is highlighted"
            />
          )}
          <dl className="cmp-channel-facts">
            {/* What it had cost by the point being LOOKED AT — served per round
                (`spend_to_round`, folded from the ledger's per-call `round`), so walking the
                branch moves it. Nothing is summed here; the round is an index.
                Falls back to the cycle's roll-up when the pick is on another lane, whose costs
                this channel's read did not fold — and says which it is showing either way. */}
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
            {/* The DATASET first, and it is the fact that was missing: on a self-optimizing board
                every channel is some `justlogic-d234__xxxxxx`, and a card naming only the six
                hex characters cannot say which of them it is a seed of. */}
            <div>
              <dt>dataset</dt>
              <dd title={reading.dataset_name}>{reading.dataset_name || "—"}</dd>
            </div>
            <div>
              <dt>campaign</dt>
              <dd title={reading.campaign_id}>{shortId(reading.campaign_id)}</dd>
            </div>
            {/* Where it LIVES, when that is not the top level: an inner searchpoint's own
                campaign id says nothing about which run opened the sandbox it is in. */}
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
            {/* WHERE on that branch the numbers above were read — the one row here that is about
                a POINT rather than a cycle, and the row the card was missing. A `course:` or
                `campaign:` channel names a branch, so without it there was nothing on screen
                saying which of its searchpoints answered. */}
            <div>
              <dt>reads at</dt>
              <dd title={reading.candidate_id}>
                {head.own?.label ?? reading.label} · round {reading.round}
              </dd>
            </div>
            {/* How deep the BRANCH went, which is not how deep that point sits — conflating the
                two is what made a candidate at round 2 of six report six. */}
            <div>
              <dt>rounds on branch</dt>
              <dd>{reading.cycle_rounds_scored}</dd>
            </div>
          </dl>
          {/* The SENTENCE is served (`comparable_note`). A different ruler and a different
              dataset are not one fact worded twice, and the copy that lived here said "its cells
              still pair where they overlap" over a pair that shared no question at all. */}
          {reading.comparable === false && (
            <p className="l4-warn">{reading.comparable_note}</p>
          )}

          {/* The MAP first, then what the point it highlights IS. Walking the cladogram is what
              chooses the subject of everything below it, so it reads top-down; under the fold the
              operator had to scroll back up past the drill-in to move the pick that drives it. */}
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

          {/* WHAT the highlighted searchpoint is, what it scored, and the editor for it — the
              same drill-in the dashboard opens on a candidate click, because it is the same
              question. It follows the pick: walking to R3.2 reads R3.2's document, which is the
              whole point of being able to walk at all — so it stays OPEN across a pick. Closing
              it when the operator walks somewhere would shut the panel at the exact moment they
              asked to see something. */}
          <details className="cmp-channel-setup" open={setupOpen}>
            <summary
              onClick={(e) => {
                e.preventDefault();
                setSetupOpen((v) => !v);
              }}
            >
              <span>
                {selected ? `${selected.label} · round ${selected.round ?? 0}` : "This searchpoint"}
              </span>
              {!pickedIsOwn && (
                <span className="l4-dim">
                  {" "}
                  — this channel reads at {head.own?.label ?? reading.label}
                </span>
              )}
              <ChannelRestore edits={edits} subjectKey={pickedKey} onEdits={onEdits} />
            </summary>
            {pipeline?.node_config_schema ? (
              <SearchpointDrillIn
                row={pickedRow}
                cfg={pickedCfg}
                samples={pickedSamples}
                arms={pickedArms}
                schema={pipeline.node_config_schema}
                outputSchema={pipeline.node_output_schema}
                // Seeded with the operator's scenario written back in, not with the bare record:
                // the values editor drops its own draft whenever the seed changes, so a restore
                // puts the inputs back by itself rather than clearing the record underneath them.
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
                    <SteerForkAction
                      candidate={selectedCandidateOf(selected, pickedPath.at(-1)?.cycleId ?? "")}
                      path={pickedPath}
                      // No stream for this branch — exactly one cycle streams and it is whichever
                      // the dashboard is parked on. The seed comes from the round file, which is
                      // the only source this tab could honestly have.
                      dash={null}
                      schema={pipeline.node_config_schema}
                      outputSchema={pipeline.node_output_schema}
                      parentIsLive={
                        index.get(encodeCyclePath(pickedPath))?.course?.run_phase === "running"
                      }
                    />
                  )
                }
              />
            ) : (
              // No schema, no editor. `configRows` answers `[]` for a null one and the editor then
              // prints "this node declares no configurable params" — which is a claim about the
              // pipeline, not about the fetch, and it would be false.
              <p className="l4-note">
                This point&rsquo;s dataset declares no pipeline on this instance, so its
                configuration cannot be shown in the pipeline&rsquo;s own terms. The table below
                lists what its round document recorded.
              </p>
            )}
          </details>
        </>
      )}
    </section>
  );
}

// WHICH of the three heads of this branch the map can highlight, as NODES rather than addresses:
// the picker selects, it does not navigate. The winner is the last candidate an election crowned;
// the most recent is the last one the highest round minted, crowned or not; the picked one is
// whatever the map has been clicked on. Nothing here computes a level — it reads the served tree.
function useChannelHead(
  index: ReturnType<typeof indexLineage>,
  reading: SubjectReading | null,
  selected: LineageNode | null,
) {
  return useMemo(() => {
    const courseKey = reading ? encodeCyclePath(readingPath(reading)) : null;
    const candidates = (courseKey && index.get(courseKey)?.candidates) || [];
    // Tree order, so the LAST node tying the max round is the most recent — no re-sort, which
    // would be an ordering this layer invented. `is_winner` alone says nothing on a round still
    // scoring, so the crown walk reads `election_held` beside it.
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
    // The crown, or — on a branch whose rounds all held — the point the channel itself reads at,
    // which is what the server resolved the course to.
    const winner = crowned ?? own;
    const latest = newest;
    // Two nodes are the same point only when their COURSE agrees too: a fork contributes an
    // attempt that keeps its own id while sitting on another course's timeline.
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

// The dashboard's own cladogram, wired to this card.
//
// It maps the CAMPAIGN, never the reading. A channel whose point answered nothing has no
// reading at all, and that is exactly the card that needs a map — refusing to draw one there
// left the operator with a card saying "pick a point that has run" above a map that would not
// open.
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
  // The tree, its index, its load state and the highlighted point are the CARD's — the head
  // picker above needs all of them before this ever opens, and a second subscription (or a
  // second `indexLineage`) here would be a second reading of one genealogy.
  root: LineageNode | null;
  index: LineageIndex;
  loaded: boolean;
  failed: boolean;
  selected: LineageNode | null;
  setSelected: (next: LineageNode | null) => void;
  // Points the operator has changed a setting on, plus everything descending from one. The
  // drawing withdraws their numbers rather than dimming them as counterfactuals — nothing ran at
  // the edited value, so there is no alternative reading to show, only an absent one.
  invalidated: ReadonlySet<string>;
  // The campaign's ROOT course, whatever branch the card reads on: the server's recursion
  // reaches every fork AND every L4 sandbox below it, so a fork channel, an inner seed and their
  // parent all share one fetch. Same rooting as `LineageProvider`, so a card on the viewed
  // campaign rides the tree already on screen instead of opening a second read of it.
  rootPath: CyclePath;
  reading: SubjectReading | null;
  // Every channel on the board, so this tree marks all of them — not only the one it reads at.
  points: readonly CladogramChannel[];
  // This card's own, which is where the drawing is CUT.
  own: CladogramChannel | null;
  subject: string;
  onReplace: (from: string, to: string) => void;
  onAdd: (channel: CompareChannel) => void;
  hasSubject: (subject: string) => boolean;
}) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  // The map is cut at this card's point by default — what came after it is no part of how it
  // came to be. Lifted for the one thing the cut takes away: the map is also the navigator, and
  // a channel cannot be moved FORWARD onto a round the drawing has stopped before.
  const [whole, setWhole] = useState(false);

  const courses = useMemo(() => (root ? walkCourses(root) : []), [root]);
  // Accuracy, never the composite: the card's own value already rides the selected metric, and a
  // node painting a second one would put two answers to "what is this worth" in one card.
  const { valueByKey, thetaByKey } = useMemo(() => nodeOverlays(courses, false), [courses]);

  // The card's own course, in the tree's address vocabulary — the highlighted lane. The served
  // `inside` chain is what makes this join at any depth: the ids alone name the leaf, and a
  // depth-1 guess never matched a node inside a sandbox.
  const viewedKey = useMemo(
    () => (reading ? encodeCyclePath(readingPath(reading)) : null),
    [reading],
  );
  // The LANES to open on — one per channel this tree holds, so both sides of the comparison are
  // reachable the moment the map opens rather than one being buried in a collapsed lane. Only
  // the tree can name a lane (`nodeKeyOf`), and a card whose channels are all elsewhere opens on
  // the ROOT's lane rather than on a wall of dots.
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
  // Compared as a STRING, not by identity: `points` is rebuilt on every evidence fetch, and
  // re-seeding on each would throw away the lanes the operator opened and disarm their click.
  const seed = laneKeys.join("~");
  const [seeded, setSeeded] = useState<string | null>(null);
  if (seed && seed !== seeded) {
    setSeeded(seed);
    setExpanded(new Set(laneKeys));
    // The SELECTION is not re-seeded here. It belongs to the card, which seeds it from this
    // channel's own head and re-seeds when the channel is re-pointed; clearing it on a lane
    // change would leave the picker above lit on nothing.
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
      // Every channel on the board, each owning its own ink — including this card's own, which is
      // why `isPicked` no longer marks it: the accent ring and the channel colour on one node
      // were two marks for one fact, and the accent one hid which channel it was.
      channels: points,
      clip: whole ? null : own,
      invalidated,
      // The highlighted searchpoint, and nothing else. It never moves the CUT — that stays on
      // this card's own point, so a walk through history leaves the drawing where it was.
      isPicked: (n: RoundNodePos) =>
        !!selected &&
        n.candidateId === selected.id &&
        n.coursePathKey === encodeCyclePath(pathOf(selected)),
      // The SERVED node the dot was placed from, which is what carries the point's numbers — the
      // drawing's node is a placed geometry, and re-deriving a searchpoint's scalars from one
      // would be a second answer to what the tree already says. It rides on the position rather
      // than being re-found by key: a lane lookup silently finds nothing for a fork-contributed
      // attempt, which is drawn on the parent's lane and indexed under the fork's.
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

  // What the drawing IS and what to do with the node clicked on it, both ABOVE it. They are the
  // map's chrome, and below it they sat between the tree and the drill-in the tree chooses the
  // subject of — so the reading order ran map → controls → the thing the map is for. The strip
  // also appears and disappears with the selection, and below the drawing that shoved the whole
  // fold down on every click.
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
      {/* DENSE, always: two channels is what the tab is for, so a card is half-width by
          construction. The labels ride each node's `<title>` and the strip above names what was
          clicked, so nothing here is unreachable — only unprinted. */}
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

// What can be done with the clicked searchpoint. Both verbs mint the SAME address; they differ
// only in whether this channel moves onto it or a new one joins beside it.
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
  // Any depth. The hops above the leaf are the sandbox chain the point lives in, and the read
  // descends them — so an L4 inner searchpoint is a channel exactly like a top-level one. It was
  // refused here for one release, which on a `promptpotter-self` campaign meant refusing almost
  // the whole tree.
  //
  // The ADDRESS is the leaf's; the channel's CAMPAIGN is the top hop's. Two different questions —
  // which point is this, and which registry campaign owns the tree it is drawn on — and answering
  // the second with the leaf is what left every inner channel unable to draw one.
  const next = candidateSubject(path, armed.id);
  // Silent on this channel's OWN point — the card seeds the selection there, and a permanent
  // strip offering a disabled "move here" beside "already a channel" is two dead buttons.
  if (next === subject) return null;
  const already = hasSubject(next);
  return (
    <p className="cmp-armed">
      <strong>{armed.label}</strong>
      <span className="l4-dim">
        {" "}
        R{armed.round ?? 0} · {armed.accuracy === null ? "—" : fmtPct0(armed.accuracy)}
      </span>
      {/* The tree carries no accuracy for it, so the evidence read will find nothing either.
          Said here rather than blocking the press: an arm still being scored is worth putting
          on the board to watch fill in, and a card that reads nothing is not an error. */}
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
