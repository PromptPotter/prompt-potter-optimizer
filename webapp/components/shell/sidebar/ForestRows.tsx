"use client";

import { type ReactNode, useMemo } from "react";
import { cx } from "@/lib/cx";
import { hasTag, tagValue } from "@/lib/hierarchy";
import { useSelectNode } from "@/lib/hooks/useSelectNode";
import { fmtPct0, fmtSigned, fmtThetaSe, sideTone } from "@/lib/format";
import { ARM_VERDICT_LABELS, THETA_CAVEAT_INFO } from "@/lib/api/types.generated";
import {
  groupByRoot,
  liftOf,
  nodeKeyOf,
  panelCellLabel,
  pathOf,
  splitRetired,
  type RetiredGroup,
  type RootGroup,
  type RowStat,
} from "@/lib/derivations";
import {
  decodeCyclePath,
  encodeCyclePath,
  nodeAddress,
  shortFamilyTail,
  type CyclePath,
} from "@/lib/ids";
import type { ArmNode, CourseLineage, HierarchyCampaign, HierarchyCourse } from "@/lib/api";
import { useLineageTree, type CampaignTree } from "@/lib/lineage";
import { useWorkspace } from "@/lib/workspace";
import { PairedLift } from "@/components/shell/PairedLift";
import { CampaignMenu } from "./CampaignMenu";
import { CampaignRowLabel, PhaseMark } from "./CampaignRowLabel";
import { CompareToggle } from "./CompareToggle";
import { RowHoverCard } from "./RowHoverCard";

export interface TreeCtx {
  isNodeOpen: (key: string, openByDefault: boolean) => boolean;
  toggleNode: (key: string) => void;
  viewedPath: CyclePath | null;
  viewedCandidateId: string | null;
  showCandidates: boolean;
}

function courseOpen(ctx: TreeCtx, path: CyclePath): boolean {
  // Closed by default: an open course fetches `/tree`.
  return ctx.showCandidates && ctx.isNodeOpen(encodeCyclePath(path), false);
}

export function ForestRows({ entries, ctx }: { entries: HierarchyCampaign[]; ctx: TreeCtx }) {
  const groups = useMemo(() => groupByRoot(entries), [entries]);
  return (
    <>
      {groups.map((group) => (
        <OriginRow key={group.rootCycleId} group={group} ctx={ctx} />
      ))}
    </>
  );
}

function GroupRow({
  addr,
  openByDefault,
  ctx,
  className,
  title,
  name,
  meta,
  children,
}: {
  addr: string;
  openByDefault: boolean;
  ctx: TreeCtx;
  className: string;
  title: string;
  name: ReactNode;
  meta?: ReactNode;
  children: ReactNode;
}) {
  const open = ctx.isNodeOpen(addr, openByDefault);
  return (
    <>
      <div className={`unit-library-family ${className}`}>
        <button
          type="button"
          className="unit-library-twist"
          onClick={() => ctx.toggleNode(addr)}
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

function OriginRow({ group, ctx }: { group: RootGroup; ctx: TreeCtx }) {
  const { rootCycleId, campaigns } = group;
  if (campaigns.length === 1) return <RunRow campaign={campaigns[0]!} ctx={ctx} />;
  return (
    <GroupRow
      addr={nodeAddress([], rootCycleId)}
      openByDefault
      ctx={ctx}
      className="origin-row"
      title={`${campaigns.length} campaigns start from this same specification (${rootCycleId}). Identical runs mean the candidates that produced them collapsed to the same prompt.`}
      name={
        <>
          spec {shortOrigin(rootCycleId)}
          <span className="unit-library-kind">{campaigns.length} runs</span>
        </>
      }
    >
      {campaigns.map((campaign) => (
        <li key={campaign.address}>
          <RunRow campaign={campaign} ctx={ctx} />
        </li>
      ))}
    </GroupRow>
  );
}

function shortOrigin(originId: string): string {
  return originId.startsWith("cycle_") ? originId.slice(6, 14) : originId.slice(0, 8);
}

function RunRow({ campaign, ctx: shared }: { campaign: HierarchyCampaign; ctx: TreeCtx }) {
  const selfOptimization = hasTag(campaign, "self_optimization");
  // Whatever the preference says: a self-optimization candidate CONTAINS its inner runs.
  const ctx = useMemo(
    () => (selfOptimization ? { ...shared, showCandidates: true } : shared),
    [shared, selfOptimization],
  );
  const rootPath = useMemo(
    () => decodeCyclePath(campaign.facts.root) ?? [],
    [campaign.facts.root],
  );
  const tree = useLineageTree(rootPath, rootPath.length > 0 && courseOpen(ctx, rootPath));

  return (
    <CourseRow
      entry={campaign}
      node={tree.root}
      path={rootPath}
      tree={tree}
      ctx={ctx}
      chrome={
        <>
          <CompareToggle campaign={campaign} />
          <CampaignMenu campaign={campaign} />
        </>
      }
    />
  );
}

function CourseRow({
  entry,
  node,
  path,
  tree,
  ctx,
  chrome,
}: {
  entry: HierarchyCampaign | HierarchyCourse;
  node: CourseLineage | null;
  path: CyclePath;
  tree: CampaignTree;
  ctx: TreeCtx;
  chrome?: ReactNode;
}) {
  const { navigate } = useWorkspace();
  const addr = encodeCyclePath(path);
  const open = courseOpen(ctx, path);
  const rows = node?.children ?? [];
  const { live: liveRows, retired: retiredGroups } = splitRetired(rows);

  const archived = entry.level === "campaign" && entry.facts.lifecycle === "archived";
  // The WHOLE path, not the leaf cycleId: `cycle_id` is an origin hash two campaigns can share.
  const selected =
    ctx.viewedPath != null &&
    encodeCyclePath(ctx.viewedPath) === encodeCyclePath(path) &&
    ctx.viewedCandidateId == null;

  const row = (
    <div className={cx("unit-library-family", selected && "selected", archived && "archived")}>
      {ctx.showCandidates ? (
        <button
          type="button"
          className="unit-library-twist"
          onClick={() => ctx.toggleNode(addr)}
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
        onClick={() => navigate(path, { resume: true })}
        aria-current={selected ? "true" : undefined}
        disabled={archived}
      >
        {entry.level === "campaign" ? (
          <CampaignRowLabel campaign={entry} />
        ) : (
          <CourseRowLabel course={entry} />
        )}
      </button>
      {chrome}
    </div>
  );

  return (
    <>
      <RowHoverCard card={entry}>{row}</RowHoverCard>
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
              <CandidateRow cand={cand} tree={tree} ctx={ctx} timeline={path} />
            </li>
          ))}
          {retiredGroups.map((group) => (
            <li key={group.branch}>
              <RetiredGroupRow group={group} tree={tree} ctx={ctx} timeline={path} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function CourseRowLabel({ course }: { course: HierarchyCourse }) {
  const { task, standing } = course.facts;
  return (
    <span className="unit-library-row">
      <span className="unit-library-name">
        {task ? panelCellLabel(task) : (tagValue(course, "dataset") ?? "")}
      </span>
      <span className="unit-library-meta unit-library-meta-marked">
        <PhaseMark status={course.state.status} />
        <span>{standing ? <PairedLift reading={standing.vs_origin} unread="label" /> : "—"}</span>
      </span>
    </span>
  );
}

function RetiredGroupRow({
  group,
  tree,
  ctx,
  timeline,
}: {
  group: RetiredGroup;
  tree: CampaignTree;
  ctx: TreeCtx;
  timeline: CyclePath;
}) {
  const n = group.candidates.length;
  const attempts = `${n} attempt${n === 1 ? "" : "s"}`;
  return (
    <GroupRow
      addr={nodeAddress(timeline, group.branch)}
      openByDefault={false}
      ctx={ctx}
      className="retired"
      title={`${attempts} the run left behind when it branched to ${group.branch} and continued there. They keep their labels and their numbers; the attempts that replaced them are on the line above.`}
      name={<span className="unit-library-kind">⑂ retired → {shortFamilyTail(group.branch)}</span>}
      meta={<span className="unit-library-status">{attempts}</span>}
    >
      {group.candidates.map((cand) => (
        <li key={nodeKeyOf(cand)}>
          <CandidateRow cand={cand} tree={tree} ctx={ctx} timeline={timeline} />
        </li>
      ))}
    </GroupRow>
  );
}

function CandidateRow({
  cand,
  tree,
  ctx,
  timeline,
}: {
  cand: ArmNode;
  tree: CampaignTree;
  ctx: TreeCtx;
  // Not necessarily the minting course: a fork's contribution differs, and deselect lands here.
  timeline: CyclePath;
}) {
  const inner = cand.children;
  const { reading, fork } = cand;
  const candPath = pathOf(cand);
  const addr = nodeKeyOf(cand);
  const open = ctx.isNodeOpen(addr, true);
  const hasChildren = inner.length > 0;
  // Keyed on the ROUND, not the label: a fork's C0 replays the candidate it was cut from.
  const isOrigin = reading.arm.round === 0;
  const cutFrom = fork?.cut_from ?? null;
  const retiredBy = cand.superseded_by;

  const cycleId = candPath[candPath.length - 1]!.cycleId;
  const { isPicked, pick } = useSelectNode({ resume: true });
  const selected = isPicked(cand);

  const lede = retiredBy
    ? `Retired: the run branched to ${shortFamilyTail(retiredBy)} and continued there. It stays as the record of what ran; nothing after it is on the line.`
    : isOrigin
      ? "This course's ORIGIN: the specification it started from, measured. Click selects it; ▶ expands what measured it."
      : fork
        ? `An attempt cut as a fork (${shortFamilyTail(cycleId)}), on this campaign's one timeline. Click selects it; the dashboard follows that fork.`
        : "A candidate this course proposed and measured. Click selects it; ▶ expands what measured it.";

  const verdict = ARM_VERDICT_LABELS[cand.verdict];

  const { theta = null, se = null, caveat = null } = reading.ability ?? {};
  const accuracy = reading.own?.accuracy?.value ?? null;
  const { scored, expected } = reading.panel;
  const lift = liftOf(reading.vs_reference);
  const stats: RowStat[] = [];
  if (theta != null || caveat != null) {
    stats.push({
      label: "Ability θ",
      value: fmtThetaSe(theta, se),
      sub: caveat
        ? "not ability — see below"
        : cand.elects_on === "ability"
          ? "what the round elects on"
          : undefined,
      className: caveat ? "summary-block-warn" : undefined,
    });
  }
  stats.push({
    label: "Accuracy",
    value: fmtPct0(accuracy),
    sub:
      scored != null ? `${scored}${expected != null ? ` of ${expected}` : ""} scored` : undefined,
  });
  if (lift != null) {
    stats.push({
      label: "Lift vs parent",
      value: fmtSigned(lift.value),
      sub: `[${fmtSigned(lift.ci_lo)}, ${fmtSigned(lift.ci_hi)}]`,
      className: sideTone(lift.side),
    });
  }
  const facts: [string, string][] = [["Round", String(reading.arm.round)], ["Cycle", cycleId]];
  if (reading.sp_hash) facts.push(["Searchpoint", reading.sp_hash]);
  if (fork?.steered_by) facts.push(["Steered by", fork.steered_by]);

  const card = {
    level: "candidate" as const,
    label: cand.label,
    state: { status: { label: verdict } },
    tags: fork ? [{ label: `⑂${cutFrom ? ` from ${cutFrom}` : " fork"}` }] : undefined,
    lede,
    stats,
    caveat: caveat ? (
      <>
        <strong>{THETA_CAVEAT_INFO[caveat].head}.</strong> {THETA_CAVEAT_INFO[caveat].body}
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
              onClick={() => ctx.toggleNode(addr)}
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
                {fork && (
                  <span
                    className="unit-library-kind"
                    title={`Cut as a fork (${cycleId})${fork.steered_by ? ` by ${fork.steered_by}` : ""} — it replays ${cutFrom ?? "its origin"} and searches on from there.`}
                  >
                    ⑂{cutFrom ? ` from ${cutFrom}` : ""}
                  </span>
                )}
                {reading.election.crown === "elected" && (
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
                {accuracy == null && fork ? (
                  <PhaseMark status={fork.status} />
                ) : (
                  fmtPct0(accuracy)
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
                entry={course.course}
                node={course}
                path={pathOf(course)}
                tree={tree}
                ctx={ctx}
              />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
