"use client";
// The sidebar tree — ONE recursive renderer for a forest at any depth:
//
//   ForestRows → OriginRow → CourseRow ⇄ CandidateRow → (inner CourseRow…)
//
// Two row kinds, alternating forever: a course (a campaign's root, a fork, an L4
// inner run — all one component) produces candidates, and measuring a candidate
// at L4 means running another course. `RunRow` and `ForkCourse` are adapters into
// that alternation, not tiers. `CourseRow` branches on what a course IS (`is_root`,
// `spawned_by`, `backend_type`), never on depth. Rendering rules that cost a
// special case to learn: ORIGIN == C0, said once (campaign row = root course row;
// C0 stays a candidate row inside it); a fork is a SIBLING course beside the
// candidate it was cut from, wears `from C0` as a badge and no C0 row of its own;
// no per-tier framing — the tree is its indent rail and labels.

import { useMemo } from "react";
import { cx } from "@/lib/cx";
import { useSelection } from "@/lib/SelectionContext";
import { isSelectedCandidate } from "@/lib/types";
import { campaignDisplayName, UNIT_KIND_LABEL } from "@/lib/names";
import { fmtPct0 } from "@/lib/format";
import { runPhaseLabel } from "@/lib/run-phase";
import {
  indexForks,
  isSelfOptimization,
  panelCellLabel,
  type ForkIndex,
  type LineageCandidate,
} from "@/lib/derivations";
import { shortFamilyTail, encodeCyclePath, type CyclePath } from "@/lib/ids";
import type { CampaignSummary, CycleListEntry } from "@/lib/api";
import { useForest } from "@/lib/hooks/useForest";
import {
  useCampaignCandidates,
  type CampaignCandidates,
} from "@/lib/hooks/useCampaignCandidates";
import { buildForest, fileInnerRuns, isNodeOpen, nodeKey } from "./grouping";
import type { OriginGroup, RunGroup } from "./grouping";
import { CampaignMenu } from "./CampaignMenu";
import { CampaignSizeHover } from "./CampaignSizeHover";

// What every row needs to render itself and answer clicks. Threaded down rather
// than context'd so the tree stays a pure function of its props.
export interface TreeCtx {
  collapsedNodes: Set<string>;
  toggleNode: (key: string) => void;
  // The viewed path's leaf — marks the selected row at whatever depth it lives.
  viewedPath: CyclePath | null;
  // The candidate the tree is parked on, if any — the bars plot ITS children, so a course
  // row is only "the viewed node" while this is null.
  viewedCandidate: string | null;
  selectCyclePath: (path: CyclePath, candidate?: string | null) => void;
  // The RESOLVED store's own pointer, for THIS ctx's forest — each forest resolves
  // its own (the active session up top, a live inner loop in its sandbox), so a
  // course rebuilds the ctx it hands to rows drawn from its inner forest.
  activeCampaignId: string | null;
  activeCycleId: string | null;
}

// Stated once because two components ask: `RunRow` gates the campaign's one
// `/lineage` read on it, and the `CourseRow` it renders gates the rows.
function courseOpen(ctx: TreeCtx, path: CyclePath): boolean {
  return isNodeOpen(ctx.collapsedNodes, "course", encodeCyclePath(path));
}

export function ForestRows({
  origins,
  at,
  ctx,
}: {
  origins: OriginGroup[];
  at: CyclePath;
  ctx: TreeCtx;
}) {
  return (
    <>
      {origins.map((origin) => (
        <OriginRow key={origin.originId} origin={origin} at={at} ctx={ctx} />
      ))}
    </>
  );
}

// A declaration and the runs that measure it. Renders as a tier only when it
// groups MORE than one run (at L4: mode collapse — two candidates whose
// meta-prompts came out identical); a lone run wears its own row.
function OriginRow({
  origin,
  at,
  ctx,
}: {
  origin: OriginGroup;
  at: CyclePath;
  ctx: TreeCtx;
}) {
  if (origin.runs.length === 1) return <RunRow run={origin.runs[0]!} at={at} ctx={ctx} />;

  const addr = `${encodeCyclePath(at)}|${origin.originId}`;
  const open = isNodeOpen(ctx.collapsedNodes, "org", addr);

  return (
    <>
      <div className="unit-library-family origin-row">
        <button
          type="button"
          className="unit-library-twist"
          onClick={() => ctx.toggleNode(nodeKey("org", addr))}
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
              <RunRow run={run} at={at} ctx={ctx} />
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

// ONE campaign: its root course, wearing the campaign's name. `/lineage` answers
// the whole campaign in one conditional round-trip, so it is fetched once here
// (gated on the root course being open) and threaded down, never per course.
function RunRow({ run, at, ctx }: { run: RunGroup; at: CyclePath; ctx: TreeCtx }) {
  const { campaign, root, branches } = run;
  const rootPath: CyclePath = [...at, { campaignId: root.campaign_id, cycleId: root.cycle_id }];
  const candidates = useCampaignCandidates(
    campaign.campaign_id,
    at,
    courseOpen(ctx, rootPath),
  );
  const forks = useMemo(() => indexForks(branches), [branches]);

  return (
    <CourseRow
      cycle={root}
      campaign={campaign}
      at={at}
      ctx={ctx}
      candidates={candidates}
      forks={forks}
      label={runLabel(run, campaign)}
      chrome={<CampaignMenu campaign={campaign} />}
      hover
      bestAccuracy={run.bestAccuracy}
    />
  );
}

// An L4 inner run is machine-minted (random id, same benchmark for every panel
// cell), so it wears the cell it measured (`spawned_by.task` tail, e.g. `seed-0`);
// a top-level campaign wears its own name.
function runLabel(run: RunGroup, campaign: CampaignSummary): string {
  const task = run.root.spawned_by?.task;
  return task ? panelCellLabel(task) : campaignDisplayName(campaign);
}

// ONE course — a campaign's root, a fork, or an L4 inner run. Its children are the
// candidates it produced (C0, C1.1, …) and the forks cut from them — siblings of
// those candidates, one level down from here.
function CourseRow({
  cycle,
  campaign,
  at,
  ctx,
  candidates,
  forks,
  label,
  cutFrom = null,
  chrome,
  hover = false,
  bestAccuracy,
}: {
  cycle: CycleListEntry;
  campaign: CampaignSummary;
  at: CyclePath;
  ctx: TreeCtx;
  candidates: CampaignCandidates;
  forks: ForkIndex;
  label: string;
  // The candidate this course was CUT FROM — a badge, never the name: the fork
  // sits beside that candidate, so wearing its name would double it.
  cutFrom?: string | null;
  chrome?: React.ReactNode;
  hover?: boolean;
  bestAccuracy?: number | null;
}) {
  const path: CyclePath = [...at, { campaignId: cycle.campaign_id, cycleId: cycle.cycle_id }];
  const addr = encodeCyclePath(path);
  const open = courseOpen(ctx, path);

  // L4: each candidate was measured by running a whole inner campaign. The sandbox
  // is keyed on the COURSE, fetched once here, split across the candidate rows.
  // Keyed on the connector KIND, never a dataset name — same predicate the panels
  // branch on.
  const isL4 = isSelfOptimization(campaign.backend_type);
  const inner = useForest(path, isL4 && open);
  // Rows drawn from the inner forest get its ● pointer, not the top-level one's —
  // the outer pointer names the active session and never matches a sandboxed cycle.
  const innerCtx: TreeCtx = useMemo(
    () => ({
      ...ctx,
      activeCampaignId: inner.activeCampaignId,
      activeCycleId: inner.activeCycleId,
    }),
    [ctx, inner.activeCampaignId, inner.activeCycleId],
  );

  const produced = candidates.byCycle.get(cycle.cycle_id) ?? [];
  const originAccuracy = produced.find((c) => c.label === "C0")?.accuracy ?? cycle.origin_accuracy;
  // A fork wears no C0 row: it borrows its origin from the candidate it was cut
  // from (the `from C0` badge names it) and replays rather than re-derives it.
  const rows = cycle.is_root ? produced : produced.filter((c) => c.label !== "C0");
  // A campaign row is handed the best across its whole fork tree (the winner often
  // lives in a fork); a fork row answers for itself.
  const best = bestAccuracy ?? cycle.best_accuracy;
  const lifted = originAccuracy != null && best != null && best !== originAccuracy;

  const archived = campaign.lifecycle_status === "archived";
  const leaf = ctx.viewedPath?.[ctx.viewedPath.length - 1];
  const selected =
    leaf?.campaignId === cycle.campaign_id &&
    leaf?.cycleId === cycle.cycle_id &&
    ctx.viewedPath?.length === path.length &&
    ctx.viewedCandidate == null;
  const active =
    cycle.campaign_id === ctx.activeCampaignId &&
    cycle.cycle_id === ctx.activeCycleId &&
    cycle.run_phase !== "checkin";
  const live = cycle.run_phase === "running";
  const statusLabel =
    !live && cycle.run_phase === "terminal" ? runPhaseLabel(cycle.run_phase, cycle.status) : null;
  const kindLabel = cycle.is_root ? null : UNIT_KIND_LABEL[cycle.unit_kind];

  const cutFromHere = forks.get(cycle.cycle_id) ?? [];

  // Inner campaigns this course spawned — the same `buildForest` the top level
  // runs on, memoized on the fetched arrays (the forest store hands every
  // subscriber the same object, so identity changes only when a poll publishes).
  const innerRuns = useMemo(
    () => buildForest(inner.campaigns, inner.cycles).flatMap((o) => o.runs),
    [inner.campaigns, inner.cycles],
  );
  const { byLabel: innerByLabel, loose: innerLoose } = fileInnerRuns(
    innerRuns,
    new Set(rows.map((c) => c.label)),
  );

  const row = (
    <div className={cx("unit-library-family", selected && "selected", archived && "archived")}>
      <button
        type="button"
        className="unit-library-twist"
        onClick={() => ctx.toggleNode(nodeKey("course", addr))}
        aria-label={open ? "Collapse" : "Expand"}
        aria-expanded={open}
        tabIndex={-1}
      >
        {open ? "▼" : "▶"}
      </button>
      <button
        type="button"
        className="unit-library-item"
        onClick={() => ctx.selectCyclePath(path)}
        aria-current={selected ? "true" : undefined}
        title={
          archived
            ? "Archived — restore it from the ⋯ menu to open"
            : `${cycle.campaign_id} · ${cycle.cycle_id}${
                cycle.spawned_by
                  ? `\n\nRan to measure ${cycle.spawned_by.candidate_label} of the course above.`
                  : cycle.is_root
                    ? "\n\nThe campaign, and the course it ran. Its origin is the C0 row inside it."
                    : `\n\nA fork. It borrows its origin from ${label} — the candidate it was cut from — and runs on from there.`
              }`
        }
        disabled={archived}
      >
        <span className="unit-library-row">
          <span className="unit-library-name">
            {label}
            {cutFrom && (
              <span
                className="unit-library-kind"
                title={`Cut from ${cutFrom} of the course above — that candidate is this fork's origin, borrowed rather than re-derived.`}
              >
                from {cutFrom}
              </span>
            )}
            {kindLabel != null && (
              <span className="unit-library-kind" title={`This course is a ${kindLabel}`}>
                {kindLabel}
              </span>
            )}
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
                {/* Origin first, and when the course has moved off it, origin → best
                    — the best is what an operator scans a sidebar for. Equal (or
                    unknown) reads as one number rather than saying it twice. */}
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
      {hover ? <CampaignSizeHover campaignId={cycle.campaign_id}>{row}</CampaignSizeHover> : row}
      {open && (
        <ul className="unit-library-children">
          {!candidates.loaded && !candidates.failed && (
            <li className="inner-library-empty">Loading candidates…</li>
          )}
          {candidates.failed && (
            <li
              className="inner-library-empty"
              title="The campaign's `/lineage` read failed. Its candidates are unknown, not absent — nothing here claims this course produced nothing."
            >
              Couldn&apos;t read candidates
            </li>
          )}
          {/* `innerLoose` counts: a fork whose only candidate was the C0 it doesn't
              render has no rows, yet its inner runs are right there. */}
          {candidates.loaded &&
            rows.length === 0 &&
            cutFromHere.length === 0 &&
            innerLoose.length === 0 && <li className="inner-library-empty">Never ran</li>}
          {rows.map((cand) => (
            <li key={`${cand.round}:${cand.candidateId}`}>
              <CandidateRow
                cand={cand}
                at={path}
                ctx={ctx}
                innerCtx={innerCtx}
                inner={innerByLabel.get(cand.label) ?? []}
              />
            </li>
          ))}
          {/* Forks, beside the candidates they were cut from — same tier, because a
              fork is a sibling course, not a part of a candidate. */}
          {cutFromHere.map((f) => (
            <li key={f.cycle_id}>
              <ForkCourse
                fork={f}
                campaign={campaign}
                at={at}
                ctx={ctx}
                candidates={candidates}
                forks={forks}
              />
            </li>
          ))}
          {/* Runs with no candidate row to nest under sit directly on the course;
              the tooltip carries what is known about them. */}
          {innerLoose.map((r) => (
            <li key={r.campaign.campaign_id}>
              <RunRow run={r} at={path} ctx={innerCtx} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

// A fork, rendered as the course it is. The id tail names it; `from C0` says where
// it started. Only a steered cut names a candidate on disk — divergence / rebase /
// sweep / diag cuts attach at round level and wear no cut badge.
function ForkCourse({
  fork,
  campaign,
  at,
  ctx,
  candidates,
  forks,
}: {
  fork: CycleListEntry;
  campaign: CampaignSummary;
  at: CyclePath;
  ctx: TreeCtx;
  candidates: CampaignCandidates;
  forks: ForkIndex;
}) {
  return (
    <CourseRow
      cycle={fork}
      campaign={campaign}
      at={at}
      ctx={ctx}
      candidates={candidates}
      forks={forks}
      label={shortFamilyTail(fork.cycle_id)}
      cutFrom={candidates.forkFromLabel.get(fork.cycle_id) ?? null}
    />
  );
}

// ONE candidate this course produced — `C0` (its origin) or `C1.1`, `C1.2`, …
// What's INSIDE it is what measured it: at L4 a whole inner campaign per panel
// cell (it may hold none — a candidate replayed from cache ran nothing).
//
// Two gestures, two controls: the TWIST expands the row in place; the LABEL parks the
// tree on this candidate — it opens the course that owns it (a candidate is a tier of the
// tree, never a hop of a path, so the course carries the address), makes the bars plot
// THIS node's children, and puts it on the shared selection axis the inspector and the
// samples panes follow. Navigating and inspecting are one gesture HERE, in the tree; a bar
// click only ever does the second.
function CandidateRow({
  cand,
  at,
  ctx,
  innerCtx,
  inner,
}: {
  cand: LineageCandidate;
  at: CyclePath;
  ctx: TreeCtx;
  innerCtx: TreeCtx;
  inner: RunGroup[];
}) {
  const addr = `${encodeCyclePath(at)}|${cand.candidateId}`;
  const open = isNodeOpen(ctx.collapsedNodes, "cand", addr);
  const hasChildren = inner.length > 0;
  const isOrigin = cand.label === "C0";
  const toggle = (): void => ctx.toggleNode(nodeKey("cand", addr));
  const { candidate, setSelectionForCandidate } = useSelection();
  const cycleId = at[at.length - 1]!.cycleId;
  const selected = isSelectedCandidate(candidate, cycleId, cand.round, cand.candidateId);
  const pick = (): void => {
    ctx.selectCyclePath(at, selected ? null : cand.label);
    setSelectionForCandidate(
      selected
        ? null
        : {
            cycle_id: cycleId,
            round: cand.round,
            candidate_id: cand.candidateId,
            label: cand.label,
            accuracy: cand.accuracy,
            is_winner: cand.isWinner,
          },
    );
  };

  return (
    <>
      <div className={cx("unit-library-family", selected && "selected")}>
        <button
          type="button"
          className="unit-library-twist"
          onClick={toggle}
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
          onClick={pick}
          aria-pressed={selected}
          title={
            isOrigin
              ? "C0 — this course's ORIGIN: the specification it started from, measured. Selects it; the ▶ twist expands what measured it."
              : `${cand.label} — a candidate this course proposed and measured. Selects it; the ▶ twist expands what measured it.`
          }
        >
          <span className="unit-library-row">
            <span className="unit-library-name">
              {cand.label}
              {cand.isWinner && cand.contested && (
                <span className="unit-library-kind" title="Elected this round's winner">
                  won
                </span>
              )}
            </span>
            <span className="unit-library-meta">{fmtPct0(cand.accuracy)}</span>
          </span>
        </button>
      </div>
      {open && hasChildren && (
        <ul className="unit-library-children">
          {inner.map((run) => (
            <li key={run.campaign.campaign_id}>
              <RunRow run={run} at={at} ctx={innerCtx} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
