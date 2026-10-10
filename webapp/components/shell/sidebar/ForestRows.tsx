"use client";

import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import { useSelectNode } from "@/lib/hooks/useSelectNode";
import { fmtPct0, fmtSigned, fmtThetaSe, sideTone } from "@/lib/format";
import { ARM_VERDICT_LABELS, THETA_CAVEAT_INFO } from "@/lib/api/types.generated";
import {
  accuracyStat,
  campaignCard,
  liftOf,
  nodeKeyOf,
  panelCellLabel,
  pathOf,
  splitRetired,
  type NodeKind,
  type OriginGroup,
  type RetiredGroup,
  type RowCardFacts,
  type RowStat,
  type RunGroup,
} from "@/lib/derivations";
import { encodeCyclePath, nodeAddress, shortFamilyTail, type CyclePath } from "@/lib/ids";
import type { ArmNode, CourseNode, RunStanding } from "@/lib/api";
import { useLineageTree, type CampaignTree } from "@/lib/lineage";
import { useWorkspace } from "@/lib/workspace";
import { PairedLift } from "@/components/shell/PairedLift";
import { CampaignMenu } from "./CampaignMenu";
import { CampaignRowLabel, PhaseMark } from "./CampaignRowLabel";
import { CompareToggle } from "./CompareToggle";
import { RowHoverCard } from "./RowHoverCard";

export interface TreeCtx {
  isNodeOpen: (kind: NodeKind, path: string) => boolean;
  toggleNode: (kind: NodeKind, path: string) => void;
  viewedPath: CyclePath | null;
  viewedCandidateId: string | null;
  showCandidates: boolean;
}

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

function RunRow({ run, ctx }: { run: RunGroup; ctx: TreeCtx }) {
  const { campaign } = run;
  const rootPath: CyclePath = [
    { campaignId: campaign.campaign_id, cycleId: campaign.root_cycle_id },
  ];
  const tree = useLineageTree(rootPath, courseOpen(ctx, rootPath));

  return (
    <CourseRow
      node={tree.root}
      path={rootPath}
      tree={tree}
      ctx={ctx}
      run={run}
      chrome={
        <>
          <CompareToggle
            campaignId={campaign.campaign_id}
            answeringCycleId={run.line.holder.cycle_id}
          />
          <CampaignMenu campaign={campaign} />
        </>
      }
    />
  );
}

function CourseRow({
  node,
  path,
  tree,
  ctx,
  run,
  chrome,
}: {
  node: CourseNode | null;
  path: CyclePath;
  tree: CampaignTree;
  ctx: TreeCtx;
  run?: RunGroup;
  chrome?: React.ReactNode;
}) {
  const { navigate } = useWorkspace();
  const addr = encodeCyclePath(path);
  const open = courseOpen(ctx, path);
  const rows = node?.children ?? [];
  const { live: liveRows, retired: retiredGroups } = splitRetired(rows);

  // A root row reads the served line ALWAYS — never the tree node it only has while expanded.
  const standing = run ? run.line.standing : (node?.run_standing ?? null);
  const status = run ? run.line.status : (node?.status ?? null);
  const label = run
    ? run.campaign.display_name
    : node?.task
      ? panelCellLabel(node.task)
      : (node?.dataset_name ?? "");

  const archived = run?.campaign.lifecycle_status === "archived";
  // The WHOLE path, not the leaf cycleId: `cycle_id` is an origin hash two campaigns can share.
  const selected =
    ctx.viewedPath != null &&
    encodeCyclePath(ctx.viewedPath) === encodeCyclePath(path) &&
    ctx.viewedCandidateId == null;

  const cycleId = path[path.length - 1]!.cycleId;
  const card: RowCardFacts = run
    ? campaignCard(run, node)
    : {
        title: label,
        state: status?.label,
        lede: node?.task
          ? "An inner run: it measured one candidate of the course above on one panel cell."
          : "The course and what it ran. Its origin is the C0 row inside it.",
        stats: innerStats(node, standing),
        facts: innerFacts(node, cycleId),
      };

  const row = (
    <div className={cx("unit-library-family", selected && "selected", archived && "archived")}>
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
        onClick={() => navigate(path, { resume: true })}
        aria-current={selected ? "true" : undefined}
        disabled={archived}
      >
        {run ? (
          <CampaignRowLabel run={run} />
        ) : (
          <span className="unit-library-row">
            <span className="unit-library-name">{label}</span>
            <span className="unit-library-meta unit-library-meta-marked">
              {status && <PhaseMark status={status} />}
              <span>
                {standing ? <PairedLift reading={standing.vs_origin} unread="label" /> : "—"}
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

function innerFacts(node: CourseNode | null, cycleId: string): [string, string][] {
  const facts: [string, string][] = [];
  if (node?.dataset_name) facts.push(["Dataset", node.dataset_name]);
  if (node?.task) facts.push(["Task", node.task]);
  facts.push(["Cycle", cycleId]);
  return facts;
}

function innerStats(node: CourseNode | null, standing: RunStanding | null) {
  const stats: RowStat[] = standing ? [accuracyStat(standing, node)] : [];
  if (standing?.stalls_left != null && standing.stalls_left_cap != null) {
    stats.push({ label: "Lives", value: `${standing.stalls_left} / ${standing.stalls_left_cap}` });
  }
  return stats;
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
  const open = ctx.isNodeOpen("cand", addr);
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
    title: cand.label,
    state: verdict,
    tags: fork ? [`⑂${cutFrom ? ` from ${cutFrom}` : " fork"}`] : undefined,
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
