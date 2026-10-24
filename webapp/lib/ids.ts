import {
  ALL_DOTS_RE,
  CYCLE_PATH_HOP_SEP,
  CYCLE_PATH_UNIT_SEP,
  ID_COMPONENT_RE,
} from "@/lib/api/types.generated";

// Mirror `store/layout.py::root_cycle_id` — change both.
const SIBLING_LAST_SEP_RE = /_(fork|diag)_(?!.*_(?:fork|diag)_)([^/]*)$/;
const SIBLING_FIRST_SEP_RE = /_(fork|diag)_/;

export function rootCycleId(cycleId: string): string {
  const m = cycleId.match(SIBLING_FIRST_SEP_RE);
  return m && m.index !== undefined ? cycleId.slice(0, m.index) : cycleId;
}

export function shortFamilyTail(cycleId: string): string {
  const m = cycleId.match(SIBLING_LAST_SEP_RE);
  const kind = m?.[1];
  const tail = m?.[2];
  if (kind && tail) return `${kind.charAt(0)}·${tail}`;
  return cycleId;
}

// A cycle_id is unique only within its campaign, so pickers key on the pair.
export const UNIT_SEP = CYCLE_PATH_UNIT_SEP;

export function unitKey(campaignId: string, cycleId: string): string {
  return `${campaignId}${UNIT_SEP}${cycleId}`;
}

// Not the generated `CycleHop`: persisted in view memory and the hash, so it must survive a server rename.
interface PathHop {
  campaignId: string;
  cycleId: string;
}
export type CyclePath = PathHop[];

const HOP_SEP = CYCLE_PATH_HOP_SEP;

// All-dots ids match the charset but are traversal segments the server refuses.
export function validIdComponent(s: string): boolean {
  return ID_COMPONENT_RE.test(s) && !ALL_DOTS_RE.test(s);
}

export function encodeCyclePath(path: CyclePath): string {
  return path.map((h) => unitKey(h.campaignId, h.cycleId)).join(HOP_SEP);
}

export function decodeCyclePath(s: string): CyclePath | null {
  if (!s) return null;
  const hops: CyclePath = [];
  for (const seg of s.split(HOP_SEP)) {
    const i = seg.indexOf(UNIT_SEP);
    if (i < 0) return null;
    const campaignId = seg.slice(0, i);
    const cycleId = seg.slice(i + UNIT_SEP.length);
    if (!validIdComponent(campaignId) || !validIdComponent(cycleId)) return null;
    hops.push({ campaignId, cycleId });
  }
  return hops.length ? hops : null;
}

// A hierarchy address below a course appends `/r{round}/{arm_key}`; the head is the course's path.
export function courseAddressOf(address: string): string {
  const cut = address.indexOf("/");
  return cut < 0 ? address : address.slice(0, cut);
}

// Only for a course or anything under one: a campaign's address names no cycle.
export function pathOfAddress(address: string): CyclePath {
  const path = decodeCyclePath(courseAddressOf(address));
  if (!path) throw new Error(`not a course address: ${address}`);
  return path;
}

// The top-level id an address files under in view memory: its first campaign, or a bare id
// (a top-level campaign's address, or an origin's cycle id, which groups campaigns).
export function ownerOfAddress(address: string): string | null {
  const top = courseAddressOf(address).split(HOP_SEP)[0] ?? "";
  const cut = top.indexOf(UNIT_SEP);
  const id = cut < 0 ? top : top.slice(0, cut);
  return validIdComponent(id) ? id : null;
}

export function pathRoot(path: CyclePath): PathHop {
  return path[0]!;
}

export function pathLeaf(path: CyclePath): PathHop {
  return path[path.length - 1]!;
}

// The `?descend=` query the dashboard route walks into `.inner/`; empty at depth 1.
export function encodeDescend(path: CyclePath): string {
  return encodeCyclePath(path.slice(1));
}
