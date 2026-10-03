// The browser address codec: `#/c/<campaign>/<cycle>…/<tab>/k/<candidate>/x/<run>/<sample>`.
// The `cycle_` prefix is stripped HERE, never in `ids.ts`, whose encoding is the `?descend=` wire.

import {
  validIdComponent,
  type CyclePath,
} from "./ids";
import {
  DEFAULT_ACCOUNT_PANE,
  DEFAULT_TAB,
  isAccountPane,
  isTab,
  type AccountPane,
  type Tab,
} from "./view-tab";

// An archive row (`domain/cells.py`), not a place in any cycle — it opens from every scope.
export interface CellAddress {
  runId: string;
  sampleId: number;
}
interface FollowAddress {
  kind: "follow";
  tab: Tab;
  cell: CellAddress | null;
}
interface CycleAddress {
  kind: "cycle";
  path: CyclePath;
  tab: Tab;
  candidateId: string | null;
  cell: CellAddress | null;
}
// Carries no cycle: the pin stays in workspace state while the modal is up.
interface AccountAddress {
  kind: "account";
  pane: AccountPane;
}
export type Address = FollowAddress | CycleAddress | AccountAddress;

const CYCLE_PREFIX = "cycle_";
const CAND_SEG = "k";
const CELL_SEG = "x";
const CYCLE_SEG = "c";
const ACCOUNT_SEG = "account";

function shortCycle(cycleId: string): string {
  return cycleId.startsWith(CYCLE_PREFIX) ? cycleId.slice(CYCLE_PREFIX.length) : cycleId;
}

function longCycle(seg: string): string {
  return CYCLE_PREFIX + seg;
}

// `workspace.tsx` writes this address as no hash at all.
export const EMPTY_ADDRESS = "#/";

export function formatAddress(a: Address): string {
  if (a.kind === "account") return `#/${ACCOUNT_SEG}/${a.pane}`;
  const segs: string[] = [];
  if (a.kind === "cycle") {
    segs.push(CYCLE_SEG);
    for (const hop of a.path) segs.push(hop.campaignId, shortCycle(hop.cycleId));
  }
  // The default view is omitted, except before a cell, so `x` never follows a bare hop.
  if (a.tab !== DEFAULT_TAB || a.cell) segs.push(a.tab);
  if (a.kind === "cycle" && a.candidateId) segs.push(CAND_SEG, a.candidateId);
  if (a.cell) segs.push(CELL_SEG, a.cell.runId, String(a.cell.sampleId));
  return `#/${segs.join("/")}`;
}

export function parseAddress(hash: string): Address | null {
  const segs = hash.replace(/^#/, "").split("/").filter(Boolean);
  if (segs.length === 0) return { kind: "follow", tab: DEFAULT_TAB, cell: null };

  if (segs[0] === ACCOUNT_SEG) {
    if (segs.length > 2) return null;
    const pane = segs[1];
    if (pane === undefined) return { kind: "account", pane: DEFAULT_ACCOUNT_PANE };
    return isAccountPane(pane) ? { kind: "account", pane } : null;
  }

  if (segs[0] !== CYCLE_SEG) {
    const tab = segs[0]!;
    if (!isTab(tab)) return null;
    const cell = parseCell(segs, 1);
    if (cell === undefined) return null;
    return { kind: "follow", tab, cell: cell?.cell ?? null };
  }

  // Hop pairs end at the first view name or `k`; a campaign id always contains `__`
  // (`campaign_ids.py::mint_campaign_id`), so it can never equal one.
  const path: CyclePath = [];
  let i = 1;
  while (i < segs.length) {
    const head = segs[i]!;
    if (isTab(head) || head === CAND_SEG) break;
    const cycle = segs[i + 1];
    if (cycle === undefined) return null;
    // Validate what was WRITTEN, before restoring the prefix: `cycle_..` passes the all-dots
    // rejection that `..` fails.
    if (!validIdComponent(head) || !validIdComponent(cycle)) return null;
    path.push({ campaignId: head, cycleId: longCycle(cycle) });
    i += 2;
  }
  if (path.length === 0) return null;

  let tab: Tab = DEFAULT_TAB;
  const next = segs[i];
  if (next !== undefined && isTab(next)) {
    tab = next;
    i += 1;
  }

  let candidateId: string | null = null;
  if (segs[i] === CAND_SEG) {
    const id = segs[i + 1];
    if (id === undefined || !validIdComponent(id)) return null;
    candidateId = id;
    i += 2;
  }
  const cell = parseCell(segs, i);
  if (cell === undefined) return null;

  return { kind: "cycle", path, tab, candidateId, cell: cell?.cell ?? null };
}

// null = no cell; undefined = malformed, since trailing junk is not ignorable.
function parseCell(segs: string[], i: number): { cell: CellAddress } | null | undefined {
  if (i === segs.length) return null;
  if (segs[i] !== CELL_SEG || i + 3 !== segs.length) return undefined;
  const runId = segs[i + 1]!;
  const sample = segs[i + 2]!;
  if (!validIdComponent(runId) || !/^\d+$/.test(sample)) return undefined;
  return { cell: { runId, sampleId: Number(sample) } };
}
