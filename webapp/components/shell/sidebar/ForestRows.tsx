import { type ReactNode, useMemo } from "react";
import { cx } from "@/lib/cx";
import { useSelectNode } from "@/lib/hooks/useSelectNode";
import {
  candidatesOf,
  figureOf,
  groupByRoot,
  hasTag,
  type RetiredGroup,
  type RootGroup,
  splitRetired,
  tagLabel,
  tagValue,
} from "@/lib/derivations";
import { encodeCyclePath, pathOfAddress } from "@/lib/ids";
import type {
  HierarchyCampaign,
  HierarchyCandidate,
  ListedCampaign,
  SubtreeCampaign,
  SubtreeCycle,
  UnstartedCampaign,
} from "@/lib/api/types";
import { useShowCandidates } from "@/lib/prefs/tree";
import { useLineageTree } from "@/lib/state/lineage";
import { useNodeToggle } from "@/lib/state/view-memory";
import { useWorkspace } from "@/lib/state/workspace";
import { PLACEHOLDER } from "@/lib/format";
import { HoverCard, ReadState } from "@/components/ui";
import { CampaignMenu } from "./CampaignMenu";
import { CampaignRowLabel, PhaseMark } from "./CampaignRowLabel";
import { CompareToggle } from "./CompareToggle";
import { RowHoverCard } from "./RowHoverCard";

// Every row starts closed; only the operator's press opens one (`view-memory.tsx::useNodeToggle`).

// The WHOLE path, not the leaf cycleId: `cycle_id` is an origin hash two campaigns can share. A
// candidate picked on the viewed path selects no cycle row.
function useIsViewed(cycleAddress: string): boolean {
  const { viewed, pick } = useWorkspace();
  return viewed != null && encodeCyclePath(viewed.path) === cycleAddress && pick === null;
}

export function ForestRows({ entries }: { entries: ListedCampaign[] }) {
  const groups = useMemo(() => groupByRoot(entries), [entries]);
  return (
    <>
      {groups.map((group) => (
        <OriginRow key={group.rootCycleId} group={group} />
      ))}
    </>
  );
}

// A header, never a fold: closed, it would hide whole campaigns.
function OriginRow({ group }: { group: RootGroup }) {
  const { rootCycleId, label, campaigns } = group;
  if (campaigns.length === 1) return <ListedRow campaign={campaigns[0]!} />;
  return (
    <>
      <div className="sidebar-tree-family origin-row">
        <span className="sidebar-tree-item origin-label">
          <HoverCard
            content={`${campaigns.length} campaigns start from this same specification (${rootCycleId}), so their origin scores pool.`}
            focusable
          >
            <span className="sidebar-tree-name">
              spec {label}
              <span className="sidebar-tree-kind">{campaigns.length} runs</span>
            </span>
          </HoverCard>
        </span>
      </div>
      <ul className="sidebar-tree-children">
        {campaigns.map((campaign) => (
          <li key={campaign.address}>
            <ListedRow campaign={campaign} />
          </li>
        ))}
      </ul>
    </>
  );
}

function ListedRow({ campaign }: { campaign: ListedCampaign }) {
  return campaign.entry_kind === "unstarted" ? (
    <UnstartedRow campaign={campaign} />
  ) : (
    <RunRow campaign={campaign} />
  );
}

// No cycle, so no subtree to open and no line to compare: a tap reopens its check-in.
function UnstartedRow({ campaign }: { campaign: UnstartedCampaign }) {
  const { checkin, navigate } = useWorkspace();
  const selected = checkin?.draftId === campaign.address;
  return (
    <RowHoverCard card={campaign}>
      <div className={cx("sidebar-tree-family", selected && "selected")}>
        <button
          type="button"
          className="sidebar-tree-item"
          onClick={() => navigate({ draftId: campaign.address })}
          aria-current={selected ? "true" : undefined}
        >
          <CampaignRowLabel campaign={campaign} />
        </button>
        <CampaignMenu campaign={campaign} />
      </div>
    </RowHoverCard>
  );
}

function RunRow({ campaign }: { campaign: HierarchyCampaign }) {
  return (
    <CampaignRow
      campaign={campaign}
      controls={
        <>
          <CompareToggle campaign={campaign} />
          <CampaignMenu campaign={campaign} />
        </>
      }
    >
      <RunSubtree address={campaign.address} />
    </CampaignRow>
  );
}

// Mounted only while its row is open: an open campaign fetches its subtree.
function RunSubtree({ address }: { address: string }) {
  const tree = useLineageTree(address, true);
  return (
    <ReadState read={tree} subject="its candidates" className="sidebar-tree-empty">
      {(root) => <CampaignBody body={root} />}
    </ReadState>
  );
}

// An inner run's subtree came with its outer's read.
function InnerCampaignRow({ campaign }: { campaign: SubtreeCampaign }) {
  return (
    <CampaignRow campaign={campaign}>
      <CampaignBody body={campaign} />
    </CampaignRow>
  );
}

function CampaignRow({
  campaign,
  controls,
  children,
}: {
  campaign: HierarchyCampaign | SubtreeCampaign;
  controls?: ReactNode;
  children: ReactNode;
}) {
  const { navigate } = useWorkspace();
  const nodes = useNodeToggle();
  const [showCandidates] = useShowCandidates();
  // A campaign's own candidates are the dashboard's candidate window, so the tree stops above them
  // unless the preference asks; a self-optimization campaign opens regardless, since each of its
  // candidates CONTAINS inner runs.
  const opens = showCandidates || hasTag(campaign, "self_optimization");
  const open = opens && nodes.isOpen(campaign.address);
  const archived = campaign.facts.lifecycle === "archived";
  // The row is its line: it opens the cycle a supersede handed it to, never the root it left.
  const line = campaign.facts.live_cycle;
  const selected = useIsViewed(line);
  return (
    <>
      <RowHoverCard card={campaign}>
        <div className={cx("sidebar-tree-family", selected && "selected", archived && "archived")}>
          {opens ? <Twist open={open} onClick={() => nodes.toggle(campaign.address)} /> : null}
          <button
            type="button"
            className="sidebar-tree-item"
            onClick={() => navigate({ path: pathOfAddress(line) }, { resume: true })}
            aria-current={selected ? "true" : undefined}
            disabled={archived}
          >
            <CampaignRowLabel campaign={campaign} />
          </button>
          {controls}
        </div>
      </RowHoverCard>
      {open && children}
    </>
  );
}

function CampaignBody({ body }: { body: SubtreeCampaign }) {
  const cycles = body.children;
  return (
    <ul className="sidebar-tree-children">
      {cycles.length === 1 ? (
        <CycleBody cycle={cycles[0]!} />
      ) : (
        cycles.map((cycle) => (
          <li key={cycle.address}>
            <CycleRow cycle={cycle} />
          </li>
        ))
      )}
    </ul>
  );
}

function CycleRow({ cycle }: { cycle: SubtreeCycle }) {
  const { navigate } = useWorkspace();
  const nodes = useNodeToggle();
  const open = nodes.isOpen(cycle.address);
  const selected = useIsViewed(cycle.address);
  return (
    <>
      <RowHoverCard card={cycle}>
        <div className={cx("sidebar-tree-family", selected && "selected")}>
          <Twist open={open} onClick={() => nodes.toggle(cycle.address)} />
          <button
            type="button"
            className="sidebar-tree-item"
            onClick={() => navigate({ path: pathOfAddress(cycle.address) }, { resume: true })}
            aria-current={selected ? "true" : undefined}
          >
            <CycleRowLabel cycle={cycle} />
          </button>
        </div>
      </RowHoverCard>
      {open && (
        <ul className="sidebar-tree-children">
          <CycleBody cycle={cycle} />
        </ul>
      )}
    </>
  );
}

function CycleBody({ cycle }: { cycle: SubtreeCycle }) {
  const rows = candidatesOf(cycle);
  const unplaced = cycle.children.filter((c): c is SubtreeCampaign => c.entry_kind === "campaign");
  const { live, retired } = splitRetired(rows);
  return (
    <>
      {rows.length === 0 && <li className="sidebar-tree-empty">Never ran</li>}
      {live.map((cand) => (
        <li key={cand.address}>
          <CandidateRow cand={cand} />
        </li>
      ))}
      {retired.map((group) => (
        <li key={group.branch}>
          <RetiredGroupRow cycle={cycle} group={group} />
        </li>
      ))}
      {unplaced.map((campaign) => (
        <li key={campaign.address}>
          <InnerCampaignRow campaign={campaign} />
        </li>
      ))}
    </>
  );
}

function Twist({ open, onClick }: { open: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      className="sidebar-tree-twist"
      onClick={onClick}
      aria-label={open ? "Collapse" : "Expand"}
      aria-expanded={open}
    >
      {open ? "▼" : "▶"}
    </button>
  );
}

function CycleRowLabel({ cycle }: { cycle: SubtreeCycle }) {
  const lift = figureOf(cycle, "lift")?.value ?? PLACEHOLDER;
  return (
    <span className="sidebar-tree-row">
      <span className="sidebar-tree-name">{cycle.label}</span>
      <span className="sidebar-tree-meta sidebar-tree-meta-marked">
        <PhaseMark entry={cycle} />
        <span title={lift}>{lift}</span>
      </span>
    </span>
  );
}

function RetiredGroupRow({ cycle, group }: { cycle: SubtreeCycle; group: RetiredGroup }) {
  const nodes = useNodeToggle();
  const key = `${cycle.address}/retired/${group.branch}`;
  const open = nodes.isOpen(key);
  const n = group.candidates.length;
  const [first] = group.candidates;
  return (
    <>
      <div className="sidebar-tree-family retired">
        <Twist open={open} onClick={() => nodes.toggle(key)} />
        <span className="sidebar-tree-item">
          <span className="sidebar-tree-row">
            <span className="sidebar-tree-name">
              <span className="sidebar-tree-kind">⑂ {tagLabel(first ?? null, "superseded")}</span>
            </span>
            <span className="sidebar-tree-meta">
              <span className="sidebar-tree-status">{`${n} attempt${n === 1 ? "" : "s"}`}</span>
            </span>
          </span>
        </span>
      </div>
      {open && (
        <ul className="sidebar-tree-children">
          {group.candidates.map((cand) => (
            <li key={cand.address}>
              <CandidateRow cand={cand} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function CandidateRow({ cand }: { cand: HierarchyCandidate }) {
  const inner = cand.children;
  const nodes = useNodeToggle();
  const open = nodes.isOpen(cand.address);
  const { isPicked, pick } = useSelectNode({ resume: true });
  const selected = isPicked(cand);
  const retired = tagValue(cand, "superseded") !== null;
  const elected = tagValue(cand, "verdict") === "elected";
  const [headline] = cand.figures;
  return (
    <>
      <RowHoverCard card={cand}>
        <div className={cx("sidebar-tree-family", selected && "selected", retired && "retired")}>
          {inner.length > 0 ? <Twist open={open} onClick={() => nodes.toggle(cand.address)} /> : null}
          <button
            type="button"
            className="sidebar-tree-item candidate-label"
            onClick={() => pick(cand)}
            aria-pressed={selected}
          >
            <span className="sidebar-tree-row">
              <span className="sidebar-tree-name">
                {cand.label}
                {elected && <span className="sidebar-tree-kind">{tagLabel(cand, "verdict")}</span>}
              </span>
              {headline && <span className="sidebar-tree-meta">{headline.value}</span>}
            </span>
          </button>
        </div>
      </RowHoverCard>
      {open && inner.length > 0 && (
        <ul className="sidebar-tree-children">
          {inner.map((campaign) => (
            <li key={campaign.address}>
              <InnerCampaignRow campaign={campaign} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
