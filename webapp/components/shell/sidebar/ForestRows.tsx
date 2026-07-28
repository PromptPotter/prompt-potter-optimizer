"use client";
// The sidebar tree — ONE recursive renderer over the served tree:
//
//   ForestRows → OriginRow → RunRow → CourseRow ⇄ CandidateRow → (CourseRow…)
//
// A course (a campaign root, an L4 inner run) produces candidates; measuring a candidate at
// L4 means running a whole course. That closes the recursion — L5+ is the same two
// components one turn deeper, and nothing here is depth-aware.
//
// `/tree` answers a campaign whole in one read; a node's children ARE its children. Nothing
// here derives genealogy. There is no fork row: a fork is not a node — its candidates sit on
// this course's timeline wearing the ⑂ stamp and its own `path`.

import { cx } from "@/lib/cx";
import { useSelectNode } from "@/lib/hooks/useSelectNode";
import { campaignDisplayName } from "@/lib/names";
import { fmtPct0 } from "@/lib/format";
import { runPhaseLabel } from "@/lib/run-phase";
import {
  candidatesOf,
  childCourses,
  cutFromLabel,
  nodeKeyOf,
  panelCellLabel,
  pathOf,
  roundSizes,
  wasElected,
} from "@/lib/derivations";
import { encodeCyclePath, nodeAddress, shortFamilyTail, type CyclePath } from "@/lib/ids";
import type { CampaignSummary, LineageNode } from "@/lib/api";
import { useLineageTree, type CampaignTree } from "@/lib/lineage";
import type { NodeKind } from "./grouping";
import type { OriginGroup, RunGroup } from "./grouping";
import { CampaignMenu } from "./CampaignMenu";
import { RowHoverCard } from "./RowHoverCard";

// What every row needs to render itself and answer clicks. Threaded down rather than
// context'd so the tree stays a pure function of its props.
export interface TreeCtx {
  // Expand/collapse, resolved per campaign by the view-memory provider. Functions rather
  // than a Set because the Set had to be GLOBAL to be passed as one value — and that is the
  // only reason one campaign's toggles ever shared a blob with another's. The campaign is
  // read off each node's own address.
  isNodeOpen: (kind: NodeKind, path: string) => boolean;
  toggleNode: (kind: NodeKind, path: string) => void;
  // The viewed address. `viewedPath` names the course; `viewedCandidateId` the node inside
  // it, or null for the course itself. Both are read off the node that was clicked —
  // nothing builds an address.
  viewedPath: CyclePath | null;
  viewedCandidateId: string | null;
  selectCyclePath: (path: CyclePath, candidateId?: string | null) => void;
  // The store's own active pointer — the workspace's session up top. An inner run's
  // liveness is `run_phase` on its own node (server-owned, I6), not a second pointer read.
  activeCampaignId: string | null;
  activeCycleId: string | null;
}

function courseOpen(ctx: TreeCtx, path: CyclePath): boolean {
  return ctx.isNodeOpen("course", encodeCyclePath(path));
}

export function ForestRows({ origins, ctx }: { origins: OriginGroup[]; ctx: TreeCtx }) {
  return (
    <>
      {origins.map((origin) => (
        <OriginRow key={origin.originId} origin={origin} ctx={ctx} />
      ))}
    </>
  );
}

// A declaration and the runs that measure it. Renders as a tier only when it groups MORE
// than one run (at L4: mode collapse — two candidates whose meta-prompts came out
// identical); a lone run wears its own row.
//
// Its address carries NO path — a declaration is not an address inside any one campaign, it
// is the set of campaigns measuring it — so the origin id is what owns its view memory
// (`ids.ts::ownerOfNodeAddress`).
function OriginRow({ origin, ctx }: { origin: OriginGroup; ctx: TreeCtx }) {
  if (origin.runs.length === 1) return <RunRow run={origin.runs[0]!} ctx={ctx} />;

  const addr = nodeAddress([], origin.originId);
  const open = ctx.isNodeOpen("org", addr);

  return (
    <>
      <div className="unit-library-family origin-row">
        <button
          type="button"
          className="unit-library-twist"
          onClick={() => ctx.toggleNode("org", addr)}
          aria-label={open ? "Collapse" : "Expand"}
          aria-expanded={open}
          tabIndex={-1}
        >
          {open ? "▼" : "▶"}
        </button>
        <span
          className="unit-library-item origin-label"
          title={`${origin.runs.length} campaigns start from this same specification (${origin.originId}). Identical runs mean the candidates that produced them collapsed to the same prompt.`}
        >
          <span className="unit-library-row">
            <span className="unit-library-name">
              spec {shortOrigin(origin.originId)}
              <span className="unit-library-kind">{origin.runs.length} runs</span>
            </span>
            <span className="unit-library-meta">{fmtPct0(origin.bestAccuracy)}</span>
          </span>
        </span>
      </div>
      {open && (
        <ul className="unit-library-children">
          {origin.runs.map((run) => (
            <li key={run.campaign.campaign_id}>
              <RunRow run={run} ctx={ctx} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

// `cycle_62839439e429` → `62839439` — the content hash that IS the declaration's id.
function shortOrigin(originId: string): string {
  return originId.startsWith("cycle_") ? originId.slice(6, 14) : originId.slice(0, 8);
}

// ONE campaign: its root course, wearing the campaign's name. `/tree` answers that course
// and everything below it in one conditional round-trip, so it is fetched once here (gated
// on the course being open) and the whole subtree renders off it — no fetch below this.
function RunRow({ run, ctx }: { run: RunGroup; ctx: TreeCtx }) {
  const { campaign, root } = run;
  const rootPath: CyclePath = [{ campaignId: root.campaign_id, cycleId: root.cycle_id }];
  const tree = useLineageTree(rootPath, courseOpen(ctx, rootPath));

  return (
    <CourseRow
      node={tree.root}
      path={rootPath}
      tree={tree}
      ctx={ctx}
      label={campaignDisplayName(campaign)}
      campaign={campaign}
      chrome={<CampaignMenu campaign={campaign} />}
      // The campaign row answers for its whole family: the winner often lives in a fork,
      // and `/cycles` already knows the max across it.
      bestAccuracy={run.bestAccuracy}
    />
  );
}

// ONE course — a campaign's root or an L4 inner run. Its children are the candidates on its
// timeline: the ones it minted, plus every attempt its forks contributed.
//
// `node` is null only for the ROOT row before its tree lands (the row must render so it can
// be expanded). Every nested course row already has its node in hand.
function CourseRow({
  node,
  path,
  tree,
  ctx,
  label,
  campaign,
  chrome,
  bestAccuracy,
}: {
  node: LineageNode | null;
  path: CyclePath;
  tree: CampaignTree;
  ctx: TreeCtx;
  label: string;
  // Only a top-level root has one — it drives the ⋯ menu and the archived state. An inner
  // run is machine-minted into a sandbox and an operator never archives one.
  campaign?: CampaignSummary;
  chrome?: React.ReactNode;
  bestAccuracy?: number | null;
}) {
  const addr = encodeCyclePath(path);
  const open = courseOpen(ctx, path);
  const rows = candidatesOf(node ?? undefined);

  const originAccuracy = node?.origin_accuracy ?? null;
  const best = bestAccuracy ?? node?.best_accuracy ?? null;
  const lifted = originAccuracy != null && best != null && best !== originAccuracy;

  const archived = campaign?.lifecycle_status === "archived";
  // Compare the WHOLE (campaign, cycle) path, not just the leaf cycleId: `cycle_id` is a
  // deterministic origin hash, so two campaigns of one origin (a re-`new`) share it. A
  // cycleId-only match lit BOTH runs when one was selected — the exact "select one, another
  // lights up" bug. The path carries campaignId at every hop, so encoded equality is the one
  // unambiguous address (webapp/CLAUDE.md § Viewed identity).
  const selected =
    ctx.viewedPath != null &&
    encodeCyclePath(ctx.viewedPath) === encodeCyclePath(path) &&
    ctx.viewedCandidateId == null;
  // The ● pointer. `run_phase` is the ONE server-owned run-state (I6) and rides the node,
  // so an inner run answers for itself — no second store read to ask "is it running?".
  const live = node?.run_phase === "running";
  const active =
    campaign?.campaign_id === ctx.activeCampaignId &&
    path[path.length - 1]?.cycleId === ctx.activeCycleId &&
    node?.run_phase !== "checkin";
  const statusLabel =
    !live && node?.run_phase === "terminal" ? runPhaseLabel("terminal", node.state) : null;

  // The one-line "what is this row" copy, shown in the hover card (it used to be a
  // native `title=` that floated over the storage card).
  const description = archived
    ? "Archived — restore it from the ⋯ menu to open"
    : node?.task
      ? `Ran to measure a candidate of the course above (${node.task}).`
      : "The campaign, and the course it ran. Its origin is the C0 row inside it.";

  const row = (
    <div className={cx("unit-library-family", selected && "selected", archived && "archived")}>
      <button
        type="button"
        className="unit-library-twist"
        onClick={() => ctx.toggleNode("course", addr)}
        aria-label={open ? "Collapse" : "Expand"}
        aria-expanded={open}
        tabIndex={-1}
      >
        {open ? "▼" : "▶"}
      </button>
      <button
        type="button"
        className="unit-library-item"
        onClick={() => ctx.selectCyclePath(path, null)}
        aria-current={selected ? "true" : undefined}
        disabled={archived}
      >
        <span className="unit-library-row">
          <span className="unit-library-name">
            {label}
            {live ? (
              <span className="unit-library-live" title="Status is running">
                ●
              </span>
            ) : active ? (
              <span className="unit-library-live active" title="Dashboard follows this run">
                ●
              </span>
            ) : null}
          </span>
          <span className="unit-library-meta">
            {archived ? (
              <span className="unit-library-status">Archived</span>
            ) : (
              <>
                {statusLabel && (
                  <>
                    <span className="unit-library-status">{statusLabel}</span>
                    {" · "}
                  </>
                )}
                {/* Origin first, and when the course has moved off it, origin → best —
                    the best is what an operator scans a sidebar for. Equal (or unknown)
                    reads as one number rather than saying it twice. */}
                {fmtPct0(originAccuracy ?? best ?? null)}
                {lifted && (
                  <>
                    <span className="unit-library-arrow" aria-label="improved to">
                      →
                    </span>
                    {fmtPct0(best)}
                  </>
                )}
              </>
            )}
          </span>
        </span>
      </button>
      {chrome}
    </div>
  );

  return (
    <>
      <RowHoverCard
        cycleId={path[path.length - 1]!.cycleId}
        description={description}
        datasetName={campaign?.dataset_name ?? node?.dataset_name ?? null}
        createdAt={campaign?.created_at ?? null}
        campaignId={campaign?.campaign_id}
      >
        {row}
      </RowHoverCard>
      {open && (
        <ul className="unit-library-children">
          {!tree.loaded && !tree.failed && <li className="inner-library-empty">Loading…</li>}
          {tree.failed && (
            <li
              className="inner-library-empty"
              title="The campaign's `/tree` read failed. Its candidates are unknown, not absent — nothing here claims this course produced nothing."
            >
              Couldn&apos;t read candidates
            </li>
          )}
          {tree.loaded && rows.length === 0 && <li className="inner-library-empty">Never ran</li>}
          {rows.map((cand) => (
            <li key={cand.id}>
              <CandidateRow cand={cand} siblings={rows} tree={tree} ctx={ctx} timeline={path} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

// ONE candidate on this course's timeline — `C0` (its origin) or `C1.1`, `C1.2`, … A
// candidate a FORK contributed wears the ⑂ stamp and `from C0`, and carries that fork's own
// address, so parking on it re-roots the dashboard onto the fork. What's INSIDE it is what
// measured it: at L4 a whole inner campaign per panel cell.
//
// Two gestures, two controls: the TWIST expands the row in place; the LABEL parks the tree
// on this node — the bars then plot ITS children, and the shared selection axis the
// inspector and samples panes follow moves with it. Navigating and inspecting are one
// gesture HERE, in the tree; a bar click only ever does the second.
function CandidateRow({
  cand,
  siblings,
  tree,
  ctx,
  timeline,
}: {
  cand: LineageNode;
  siblings: readonly LineageNode[];
  tree: CampaignTree;
  ctx: TreeCtx;
  // The course whose TIMELINE this row renders on — not necessarily the course that minted
  // the candidate. They differ for a fork's contribution, and that difference is what
  // deselecting has to land on (see `pick`).
  timeline: CyclePath;
}) {
  const inner = childCourses(cand);
  const candPath = pathOf(cand);
  const addr = nodeKeyOf(cand);
  const open = ctx.isNodeOpen("cand", addr);
  const hasChildren = inner.length > 0;
  // Keyed on the ROUND, not the label: a fork's C0 is a replay of the candidate it was
  // cut from, and label-matching handed it this course's origin copy.
  const isOrigin = (cand.round ?? 0) === 0;
  const cutFrom = cutFromLabel(cand, siblings);
  const elected = wasElected(cand.is_winner, roundSizes(siblings).get(cand.round ?? 0) ?? 1);

  // The navigate+inspect pair lives in `useSelectNode` — the time-ray fires the same
  // gesture on a candidate step, and both have to resolve the measurement off the node
  // rather than supply one.
  const cycleId = candPath[candPath.length - 1]!.cycleId;
  const { isPicked, pick } = useSelectNode(ctx.selectCyclePath);
  const selected = isPicked(cand);

  const description = isOrigin
    ? "C0 — this course's ORIGIN: the specification it started from, measured. Selects it; the ▶ twist expands what measured it."
    : cand.course_kind
      ? `${cand.label} — an attempt you cut as a fork (${shortFamilyTail(cycleId)}), on this campaign's one timeline. Selects it; the dashboard follows that fork.`
      : `${cand.label} — a candidate this course proposed and measured. Selects it; the ▶ twist expands what measured it.`;

  return (
    <>
      <RowHoverCard cycleId={cycleId} description={description}>
        <div className={cx("unit-library-family", selected && "selected")}>
          <button
            type="button"
            className="unit-library-twist"
            onClick={() => ctx.toggleNode("cand", addr)}
            aria-label={open ? "Collapse" : "Expand"}
            aria-expanded={open}
            disabled={!hasChildren}
            tabIndex={-1}
          >
            {!hasChildren ? "" : open ? "▼" : "▶"}
          </button>
          <button
            type="button"
            className="unit-library-item candidate-label"
            onClick={() => pick(cand, timeline)}
            aria-pressed={selected}
          >
            <span className="unit-library-row">
              <span className="unit-library-name">
                {cand.label}
                {cand.course_kind && (
                  <span
                    className="unit-library-kind"
                    title={`Cut as a fork (${cycleId})${cand.steered_by ? ` by ${cand.steered_by}` : ""} — it replays ${cutFrom ?? "its origin"} and searches on from there.`}
                  >
                    ⑂{cutFrom ? ` from ${cutFrom}` : ""}
                  </span>
                )}
                {elected && (
                  <span className="unit-library-kind" title="Elected this round's winner">
                    won
                  </span>
                )}
              </span>
              <span className="unit-library-meta">
                {/* A cut that broke before measuring anything has no number, and must not
                    borrow the origin's — that would report a fitness nothing measured. */}
                {cand.accuracy == null && cand.course_kind ? (
                  <span className="unit-library-status">
                    {runPhaseLabel("terminal", cand.state)}
                  </span>
                ) : (
                  fmtPct0(cand.accuracy)
                )}
              </span>
            </span>
          </button>
        </div>
      </RowHoverCard>
      {open && hasChildren && (
        <ul className="unit-library-children">
          {inner.map((course) => (
            <li key={encodeCyclePath(pathOf(course))}>
              <CourseRow
                node={course}
                path={pathOf(course)}
                tree={tree}
                ctx={ctx}
                label={course.task ? panelCellLabel(course.task) : course.dataset_name}
              />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
