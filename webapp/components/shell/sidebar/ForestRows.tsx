"use client";

import { type ReactNode, useMemo } from "react";
import { cx } from "@/lib/cx";
import { hasTag, tagLabel, tagValue } from "@/lib/hierarchy";
import { useSelectNode } from "@/lib/hooks/useSelectNode";
import {
  childNodes,
  coursesOf,
  groupByRoot,
  panelCellLabel,
  splitRetired,
  type RetiredGroup,
  type RootGroup,
} from "@/lib/derivations";
import { encodeCyclePath, pathLeaf, pathOfAddress, shortFamilyTail, type CyclePath } from "@/lib/ids";
import type { HierarchyCampaign, HierarchyCandidate, HierarchyCourse } from "@/lib/api";
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
      addr={rootCycleId}
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
  const { navigate } = useWorkspace();
  const rootPath = useMemo(() => pathOfAddress(campaign.facts.root), [campaign.facts.root]);
  // Closed by default: an open campaign fetches its subtree.
  const open = ctx.showCandidates && ctx.isNodeOpen(campaign.address, false);
  const tree = useLineageTree(campaign.address, open);
  const archived = campaign.facts.lifecycle === "archived";
  // The WHOLE path, not the leaf cycleId: `cycle_id` is an origin hash two campaigns can share.
  const selected =
    ctx.viewedPath != null &&
    encodeCyclePath(ctx.viewedPath) === campaign.facts.root &&
    ctx.viewedCandidateId == null;

  return (
    <>
      <RowHoverCard card={campaign}>
        <div className={cx("unit-library-family", selected && "selected", archived && "archived")}>
          {ctx.showCandidates ? (
            <Twist open={open} onClick={() => ctx.toggleNode(campaign.address)} />
          ) : null}
          <button
            type="button"
            className="unit-library-item"
            onClick={() => navigate(rootPath, { resume: true })}
            aria-current={selected ? "true" : undefined}
            disabled={archived}
          >
            <CampaignRowLabel campaign={campaign} />
          </button>
          <CompareToggle campaign={campaign} />
          <CampaignMenu campaign={campaign} />
        </div>
      </RowHoverCard>
      {open && <CampaignBody body={tree.root} tree={tree} ctx={ctx} />}
    </>
  );
}

function CampaignBody({
  body,
  tree,
  ctx,
}: {
  body: HierarchyCampaign | null;
  tree: CampaignTree;
  ctx: TreeCtx;
}) {
  const courses = body ? coursesOf(body) : [];
  return (
    <ul className="unit-library-children">
      {!tree.loaded && !tree.failed && <li className="inner-library-empty">Loading…</li>}
      {tree.failed && (
        <li
          className="inner-library-empty"
          title="The campaign's subtree read failed. Its candidates are unknown, not absent — nothing here claims this campaign produced nothing."
        >
          Couldn&apos;t read candidates
        </li>
      )}
      {courses.length === 1 ? (
        <CourseBody course={courses[0]!} ctx={ctx} />
      ) : (
        courses.map((course) => (
          <li key={course.address}>
            <CourseRow course={course} ctx={ctx} />
          </li>
        ))
      )}
    </ul>
  );
}

function CourseRow({ course, ctx }: { course: HierarchyCourse; ctx: TreeCtx }) {
  const { navigate } = useWorkspace();
  const path = pathOfAddress(course.address);
  const open = ctx.isNodeOpen(course.address, true);
  const selected =
    ctx.viewedPath != null &&
    encodeCyclePath(ctx.viewedPath) === course.address &&
    ctx.viewedCandidateId == null;
  return (
    <>
      <RowHoverCard card={course}>
        <div className={cx("unit-library-family", selected && "selected")}>
          <Twist open={open} onClick={() => ctx.toggleNode(course.address)} />
          <button
            type="button"
            className="unit-library-item"
            onClick={() => navigate(path, { resume: true })}
            aria-current={selected ? "true" : undefined}
          >
            <CourseRowLabel course={course} />
          </button>
        </div>
      </RowHoverCard>
      {open && (
        <ul className="unit-library-children">
          <CourseBody course={course} ctx={ctx} />
        </ul>
      )}
    </>
  );
}

function CourseBody({ course, ctx }: { course: HierarchyCourse; ctx: TreeCtx }) {
  const rows = childNodes(course) as HierarchyCandidate[];
  const { live, retired } = splitRetired(rows);
  return (
    <>
      {rows.length === 0 && <li className="inner-library-empty">Never ran</li>}
      {live.map((cand) => (
        <li key={cand.address}>
          <CandidateRow cand={cand} ctx={ctx} />
        </li>
      ))}
      {retired.map((group) => (
        <li key={group.branch}>
          <RetiredGroupRow course={course} group={group} ctx={ctx} />
        </li>
      ))}
    </>
  );
}

function Twist({ open, onClick }: { open: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      className="unit-library-twist"
      onClick={onClick}
      aria-label={open ? "Collapse" : "Expand"}
      aria-expanded={open}
      tabIndex={-1}
    >
      {open ? "▼" : "▶"}
    </button>
  );
}

function CourseRowLabel({ course }: { course: HierarchyCourse }) {
  const { task, standing } = course.facts;
  return (
    <span className="unit-library-row">
      <span className="unit-library-name">
        {task ? panelCellLabel(task) : course.label}
      </span>
      <span className="unit-library-meta unit-library-meta-marked">
        <PhaseMark status={course.state.status} />
        <span>{standing ? <PairedLift reading={standing.vs_origin} unread="label" /> : "—"}</span>
      </span>
    </span>
  );
}

function RetiredGroupRow({
  course,
  group,
  ctx,
}: {
  course: HierarchyCourse;
  group: RetiredGroup;
  ctx: TreeCtx;
}) {
  const n = group.candidates.length;
  const attempts = `${n} attempt${n === 1 ? "" : "s"}`;
  const branch = pathLeaf(pathOfAddress(group.branch)).cycleId;
  return (
    <GroupRow
      addr={`${course.address}/retired/${group.branch}`}
      openByDefault={false}
      ctx={ctx}
      className="retired"
      title={`${attempts} the run left behind when it branched to ${branch} and continued there. They keep their labels and their numbers; the attempts that replaced them are on that course.`}
      name={<span className="unit-library-kind">⑂ retired → {shortFamilyTail(branch)}</span>}
      meta={<span className="unit-library-status">{attempts}</span>}
    >
      {group.candidates.map((cand) => (
        <li key={cand.address}>
          <CandidateRow cand={cand} ctx={ctx} />
        </li>
      ))}
    </GroupRow>
  );
}

function CandidateRow({ cand, ctx }: { cand: HierarchyCandidate; ctx: TreeCtx }) {
  const inner = cand.children.filter((c): c is HierarchyCampaign => c.level === "campaign");
  const open = ctx.isNodeOpen(cand.address, true);
  const { isPicked, pick } = useSelectNode({ resume: true });
  const selected = isPicked(cand);
  const retired = tagValue(cand, "superseded") !== null;
  const elected = tagValue(cand, "verdict") === "elected";
  const accuracy = cand.stats.find((s) => s.key === "accuracy")?.value ?? "—";
  return (
    <>
      <RowHoverCard card={cand}>
        <div className={cx("unit-library-family", selected && "selected", retired && "retired")}>
          {inner.length > 0 ? (
            <Twist open={open} onClick={() => ctx.toggleNode(cand.address)} />
          ) : null}
          <button
            type="button"
            className="unit-library-item candidate-label"
            onClick={() => pick(cand)}
            aria-pressed={selected}
          >
            <span className="unit-library-row">
              <span className="unit-library-name">
                {cand.label}
                {elected && <span className="unit-library-kind">{tagLabel(cand, "verdict")}</span>}
              </span>
              <span className="unit-library-meta">{accuracy}</span>
            </span>
          </button>
        </div>
      </RowHoverCard>
      {open && inner.length > 0 && (
        <ul className="unit-library-children">
          {inner.map((campaign) => (
            <li key={campaign.address}>
              <InnerCampaignRow campaign={campaign} ctx={ctx} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

const LOADED: CampaignTree = { root: null, loaded: true, failed: false };

function InnerCampaignRow({ campaign, ctx }: { campaign: HierarchyCampaign; ctx: TreeCtx }) {
  const { navigate } = useWorkspace();
  const open = ctx.isNodeOpen(campaign.address, true);
  const rootPath = pathOfAddress(campaign.facts.root);
  return (
    <>
      <RowHoverCard card={campaign}>
        <div className="unit-library-family">
          <Twist open={open} onClick={() => ctx.toggleNode(campaign.address)} />
          <button
            type="button"
            className="unit-library-item"
            onClick={() => navigate(rootPath, { resume: true })}
          >
            <CampaignRowLabel campaign={campaign} />
          </button>
        </div>
      </RowHoverCard>
      {open && <CampaignBody body={campaign} tree={LOADED} ctx={ctx} />}
    </>
  );
}
