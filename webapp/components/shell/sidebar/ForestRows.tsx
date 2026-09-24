"use client";
// The sidebar tree — ONE recursive renderer over the served `/tree`:
//   ForestRows → OriginRow → RunRow → CourseRow ⇄ CandidateRow → (CourseRow…)

import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import { useSelectNode } from "@/lib/hooks/useSelectNode";
import { campaignDisplayName } from "@/lib/names";
import { effectTone, fmtPct0, fmtSigned } from "@/lib/format";
import { runPhaseLabel, runPhaseMark } from "@/lib/run-phase";
import { CAVEAT_COPY } from "@/components/candidates/AbilityInfo";
import {
  accuracyStat,
  campaignCard,
  campaignLineParts,
  campaignStatus,
  campaignVendors,
  candidatesOf,
  childCourses,
  cutFromLabel,
  nodeKeyOf,
  panelCellLabel,
  pathOf,
  roundSizes,
  spendLabel,
  splitRetired,
  wasElected,
  type NodeKind,
  type OriginGroup,
  type RetiredGroup,
  type RowCardFacts,
  type RowStat,
  type RowStatus,
  type RunGroup,
} from "@/lib/derivations";
import { encodeCyclePath, nodeAddress, shortFamilyTail, type CyclePath } from "@/lib/ids";
import type { LineageNode } from "@/lib/api";
import { useLineageTree, type CampaignTree } from "@/lib/lineage";
import { CampaignMenu } from "./CampaignMenu";
import { CampaignRowLabel, PhaseMark } from "./CampaignRowLabel";
import { CompareToggle } from "./CompareToggle";
import { RowHoverCard } from "./RowHoverCard";

export interface TreeCtx {
  isNodeOpen: (kind: NodeKind, path: string) => boolean;
  toggleNode: (kind: NodeKind, path: string) => void;
  viewedPath: CyclePath | null;
  viewedCandidateId: string | null;
  selectCyclePath: (path: CyclePath, candidateId?: string | null) => void;
  // `lib/tree-prefs.ts`. Off, a campaign is the last row of its branch and its `/tree` never loads.
  showCandidates: boolean;
}

// One gate for both readers — the row that draws the children and the fetch that supplies them.
function courseOpen(ctx: TreeCtx, path: CyclePath): boolean {
  return ctx.showCandidates && ctx.isNodeOpen("course", encodeCyclePath(path));
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

// A grouping, not an address: nothing here is selectable, so the row only opens and closes.
function GroupRow({
  kind,
  addr,
  ctx,
  className,
  title,
  name,
  meta,
  children,
}: {
  kind: NodeKind;
  addr: string;
  ctx: TreeCtx;
  className: string;
  title: string;
  name: ReactNode;
  meta?: ReactNode;
  children: ReactNode;
}) {
  const open = ctx.isNodeOpen(kind, addr);
  return (
    <>
      <div className={`unit-library-family ${className}`}>
        <button
          type="button"
          className="unit-library-twist"
          onClick={() => ctx.toggleNode(kind, addr)}
          aria-label={open ? "Collapse" : "Expand"}
          aria-expanded={open}
          tabIndex={-1}
        >
          {open ? "▼" : "▶"}
        </button>
        <span className="unit-library-item origin-label" title={title}>
          <span className="unit-library-row">
            <span className="unit-library-name">{name}</span>
            <span className="unit-library-meta">{meta}</span>
          </span>
        </span>
      </div>
      {open && <ul className="unit-library-children">{children}</ul>}
    </>
  );
}

function OriginRow({ origin, ctx }: { origin: OriginGroup; ctx: TreeCtx }) {
  if (origin.runs.length === 1) return <RunRow run={origin.runs[0]!} ctx={ctx} />;
  return (
    <GroupRow
      kind="org"
      addr={nodeAddress([], origin.originId)}
      ctx={ctx}
      className="origin-row"
      title={`${origin.runs.length} campaigns start from this same specification (${origin.originId}). Identical runs mean the candidates that produced them collapsed to the same prompt.`}
      name={
        <>
          spec {shortOrigin(origin.originId)}
          <span className="unit-library-kind">{origin.runs.length} runs</span>
        </>
      }
      meta={fmtPct0(origin.bestAccuracy)}
    >
      {origin.runs.map((run) => (
        <li key={run.campaign.campaign_id}>
          <RunRow run={run} ctx={ctx} />
        </li>
      ))}
    </GroupRow>
  );
}

function shortOrigin(originId: string): string {
  return originId.startsWith("cycle_") ? originId.slice(6, 14) : originId.slice(0, 8);
}

// The one `/tree` fetch for the whole subtree — no fetch below this.
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
      run={run}
      chrome={
        <>
          <CompareToggle
            campaignId={campaign.campaign_id}
            answeringCycleId={run.answering.cycle_id}
          />
          <CampaignMenu campaign={campaign} />
        </>
      }
      // Always `/cycles`, never the tree node the row only has while expanded.
      phase={run.answering.run_phase}
      phaseReason={run.answering.status}
    />
  );
}

// A campaign's root or an L4 inner run. `node` is null only for the root before its tree lands.
function CourseRow({
  node,
  path,
  tree,
  ctx,
  label,
  run,
  chrome,
  phase,
  phaseReason,
}: {
  node: LineageNode | null;
  path: CyclePath;
  tree: CampaignTree;
  ctx: TreeCtx;
  label: string;
  // Top-level root only; an inner run carries no campaign.
  run?: RunGroup;
  chrome?: React.ReactNode;
  // Handed in, never picked here: `/cycles` for a root row, the tree node for a nested course.
  phase: string | null | undefined;
  phaseReason: string | null | undefined;
}) {
  const addr = encodeCyclePath(path);
  const open = courseOpen(ctx, path);
  const rows = candidatesOf(node ?? undefined);
  const { live: liveRows, retired: retiredGroups } = splitRetired(rows);

  const originAccuracy = node?.origin_accuracy ?? null;
  const best = run?.bestAccuracy ?? node?.best_accuracy ?? null;
  const lifted = originAccuracy != null && best != null && best !== originAccuracy;

  const archived = run?.campaign.lifecycle_status === "archived";
  // The WHOLE path, not the leaf cycleId: `cycle_id` is an origin hash two campaigns can share.
  const selected =
    ctx.viewedPath != null &&
    encodeCyclePath(ctx.viewedPath) === encodeCyclePath(path) &&
    ctx.viewedCandidateId == null;
  const status: RowStatus | null = run
    ? campaignStatus(run)
    : phase
      ? { mark: runPhaseMark(phase, phaseReason), word: runPhaseLabel(phase, phaseReason) }
      : null;

  const cycleId = path[path.length - 1]!.cycleId;
  const card: RowCardFacts = run
    ? campaignCard(run, node, status?.word)
    : {
        title: label,
        state: status?.word,
        lede: node?.task
          ? "An inner run: it measured one candidate of the course above on one panel cell."
          : "The course and what it ran. Its origin is the C0 row inside it.",
        stats: innerStats(node, originAccuracy, best),
        facts: innerFacts(node, cycleId),
      };

  const row = (
    <div className={cx("unit-library-family", selected && "selected", archived && "archived")}>
      {/* No inert ▶ when there is nothing to expand into (frontend-surface-contract § I3). */}
      {ctx.showCandidates ? (
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
      ) : null}
      <button
        type="button"
        className="unit-library-item"
        onClick={() => ctx.selectCyclePath(path, null)}
        aria-current={selected ? "true" : undefined}
        disabled={archived}
      >
        {run ? (
          <CampaignRowLabel
            name={label}
            status={status}
            spend={spendLabel(run.campaign)}
            parts={campaignLineParts(run)}
            vendors={campaignVendors(run)}
          />
        ) : (
          <span className="unit-library-row">
            <span className="unit-library-name">{label}</span>
            <span className="unit-library-meta unit-library-meta-marked">
              {status && <PhaseMark status={status} />}
              <span>
                {fmtPct0(originAccuracy ?? best ?? null)}
                {lifted && (
                  <>
                    <span className="unit-library-arrow" aria-label="improved to">
                      →
                    </span>
                    {fmtPct0(best)}
                  </>
                )}
              </span>
            </span>
          </span>
        )}
      </button>
      {chrome}
    </div>
  );

  return (
    <>
      <RowHoverCard card={card}>{row}</RowHoverCard>
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
          {liveRows.map((cand) => (
            <li key={nodeKeyOf(cand)}>
              <CandidateRow cand={cand} siblings={rows} tree={tree} ctx={ctx} timeline={path} />
            </li>
          ))}
          {retiredGroups.map((group) => (
            <li key={group.branch}>
              <RetiredGroupRow
                group={group}
                siblings={rows}
                tree={tree}
                ctx={ctx}
                timeline={path}
              />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function innerFacts(node: LineageNode | null, cycleId: string): [string, string][] {
  const facts: [string, string][] = [];
  if (node?.dataset_name) facts.push(["Dataset", node.dataset_name]);
  if (node?.task) facts.push(["Task", node.task]);
  facts.push(["Cycle", cycleId]);
  return facts;
}

function innerStats(node: LineageNode | null, origin: number | null, best: number | null) {
  const stats: RowStat[] = [accuracyStat(origin, best)];
  if (node?.hearts != null && node.lives_cap != null) {
    stats.push({ label: "Lives", value: `${node.hearts} / ${node.lives_cap}` });
  }
  return stats;
}

function RetiredGroupRow({
  group,
  siblings,
  tree,
  ctx,
  timeline,
}: {
  group: RetiredGroup;
  siblings: readonly LineageNode[];
  tree: CampaignTree;
  ctx: TreeCtx;
  timeline: CyclePath;
}) {
  const n = group.candidates.length;
  const attempts = `${n} attempt${n === 1 ? "" : "s"}`;
  return (
    <GroupRow
      kind="retired"
      addr={nodeAddress(timeline, group.branch)}
      ctx={ctx}
      className="retired"
      title={`${attempts} the run left behind when it branched to ${group.branch} and continued there. They keep their labels and their numbers; the attempts that replaced them are on the line above.`}
      name={<span className="unit-library-kind">⑂ retired → {shortFamilyTail(group.branch)}</span>}
      meta={<span className="unit-library-status">{attempts}</span>}
    >
      {group.candidates.map((cand) => (
        <li key={nodeKeyOf(cand)}>
          <CandidateRow cand={cand} siblings={siblings} tree={tree} ctx={ctx} timeline={timeline} />
        </li>
      ))}
    </GroupRow>
  );
}

// Navigating and inspecting are one gesture here, in the tree; a bar click only ever inspects.
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
  // Not necessarily the minting course: a fork's contribution differs, and deselect lands here.
  timeline: CyclePath;
}) {
  const inner = childCourses(cand);
  const candPath = pathOf(cand);
  const addr = nodeKeyOf(cand);
  const open = ctx.isNodeOpen("cand", addr);
  const hasChildren = inner.length > 0;
  // Keyed on the ROUND, not the label: a fork's C0 replays the candidate it was cut from.
  const isOrigin = (cand.round ?? 0) === 0;
  const cutFrom = cutFromLabel(cand, siblings);
  const elected = wasElected(cand.is_winner, roundSizes(siblings).get(cand.round ?? 0) ?? 1);
  const retiredBy = cand.superseded_by;

  const cycleId = candPath[candPath.length - 1]!.cycleId;
  const { isPicked, pick } = useSelectNode(ctx.selectCyclePath);
  const selected = isPicked(cand);

  const lede = retiredBy
    ? `Retired: the run branched to ${shortFamilyTail(retiredBy)} and continued there. It stays as the record of what ran; nothing after it is on the line.`
    : isOrigin
      ? "This course's ORIGIN: the specification it started from, measured. Click selects it; ▶ expands what measured it."
      : cand.course_kind
        ? `An attempt cut as a fork (${shortFamilyTail(cycleId)}), on this campaign's one timeline. Click selects it; the dashboard follows that fork.`
        : "A candidate this course proposed and measured. Click selects it; ▶ expands what measured it.";

  const verdict = retiredBy
    ? "retired"
    : cand.status === "invalid"
      ? "invalid — never measured"
      : isOrigin
        ? "origin"
        : elected
          ? "won its round"
          : cand.election_held
            ? "not elected"
            : cand.status === "minted"
              ? "not measured yet"
              : "awaiting election";

  const caveat = cand.theta_caveat;
  const lift = cand.matched_parent_lift;
  const liftLo = cand.matched_parent_lift_ci_lo;
  const liftHi = cand.matched_parent_lift_ci_hi;
  const stats: RowStat[] = [
    {
      label: "Ability θ",
      value:
        cand.theta == null
          ? "—"
          : `${cand.theta.toFixed(2)}${cand.theta_se != null ? ` ± ${cand.theta_se.toFixed(2)}` : ""}`,
      sub: caveat ? "not ability — see below" : "what the round elects on",
      className: caveat ? "rowhover-tone-warn" : undefined,
    },
    {
      label: "Accuracy",
      value: fmtPct0(cand.accuracy),
      sub:
        cand.scored_samples != null
          ? `${cand.scored_samples}${cand.expected_samples != null ? ` of ${cand.expected_samples}` : ""} scored`
          : undefined,
    },
  ];
  if (lift != null) {
    stats.push({
      label: "Lift vs parent",
      value: fmtSigned(lift),
      sub: liftLo != null && liftHi != null ? `[${fmtSigned(liftLo)}, ${fmtSigned(liftHi)}]` : "no interval",
      className: effectTone(liftLo, liftHi),
    });
  }
  const facts: [string, string][] = [["Round", String(cand.round ?? 0)], ["Cycle", cycleId]];
  if (cand.sp_hash) facts.push(["Searchpoint", cand.sp_hash]);
  if (cand.steered_by) facts.push(["Steered by", cand.steered_by]);

  const card = {
    title: cand.label,
    state: verdict,
    tags: cand.course_kind ? [`⑂${cutFrom ? ` from ${cutFrom}` : " fork"}`] : undefined,
    lede,
    stats,
    caveat: caveat ? (
      <>
        <strong>{CAVEAT_COPY[caveat].head}.</strong> {CAVEAT_COPY[caveat].body}
      </>
    ) : undefined,
    facts,
  };

  return (
    <>
      <RowHoverCard card={card}>
        <div className={cx("unit-library-family", selected && "selected", retiredBy && "retired")}>
          {hasChildren ? (
            <button
              type="button"
              className="unit-library-twist"
              onClick={() => ctx.toggleNode("cand", addr)}
              aria-label={open ? "Collapse" : "Expand"}
              aria-expanded={open}
              tabIndex={-1}
            >
              {open ? "▼" : "▶"}
            </button>
          ) : null}
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
                {retiredBy && (
                  <span
                    className="unit-library-kind"
                    title={`Retired — the run branched to ${shortFamilyTail(retiredBy)} and continued there. Kept as the record of what ran.`}
                  >
                    retired
                  </span>
                )}
              </span>
              <span className="unit-library-meta">
                {/* A cut that broke before measuring must not borrow the origin's number. */}
                {cand.accuracy == null && cand.course_kind ? (
                  <PhaseMark
                    status={{
                      mark: runPhaseMark("terminal", cand.status),
                      word: runPhaseLabel("terminal", cand.status),
                    }}
                  />
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
                // `/cycles` lists top-level cycles only, so an inner run answers off its node.
                phase={course.run_phase}
                phaseReason={course.status}
              />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
