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

// An archive answer (`domain/cells.py`), not a place in any cycle — it opens from every scope.
export interface CellAddress {
  answer: string;
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
  // An offset into the LEAF's ledger; a followed view has none.
  at: number | null;
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
const AT_SEG = "t";
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
  const at = a.kind === "cycle" ? a.at : null;
  // The default view is omitted, except before a moment or a cell, so neither follows a bare hop.
  if (a.tab !== DEFAULT_TAB || a.cell || at !== null) segs.push(a.tab);
  if (a.kind === "cycle" && a.candidateId) segs.push(CAND_SEG, a.candidateId);
  if (at !== null) segs.push(AT_SEG, String(at));
  if (a.cell) segs.push(CELL_SEG, a.cell.answer);
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

  // A campaign id always contains `__` (`campaign_ids.py::mint_campaign_id`), so it never equals a view name or `k`.
  const path: CyclePath = [];
  let i = 1;
  while (i < segs.length) {
    const head = segs[i]!;
    if (isTab(head) || head === CAND_SEG) break;
    const cycle = segs[i + 1];
    if (cycle === undefined) return null;
    // Validate BEFORE restoring the prefix: `cycle_..` passes the all-dots rejection that `..` fails.
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
  let at: number | null = null;
  if (segs[i] === AT_SEG) {
    const offset = segs[i + 1];
    if (offset === undefined || !/^\d+$/.test(offset)) return null;
    at = Number(offset);
    i += 2;
  }
  const cell = parseCell(segs, i);
  if (cell === undefined) return null;

  return { kind: "cycle", path, tab, candidateId, at, cell: cell?.cell ?? null };
}

// null = no cell; undefined = malformed, since trailing junk is not ignorable.
function parseCell(segs: string[], i: number): { cell: CellAddress } | null | undefined {
  if (i === segs.length) return null;
  if (segs[i] !== CELL_SEG || i + 2 !== segs.length) return undefined;
  const answer = segs[i + 1]!;
  if (!validIdComponent(answer)) return undefined;
  return { cell: { answer } };
}
