// Cycle-id helpers. The sibling regexes mirror `store/layout.py::root_cycle_id` — change both;
// the address grammar is generated from `domain/cycle_paths.py`.

import {
  ALL_DOTS_RE,
  CYCLE_PATH_HOP_SEP,
  CYCLE_PATH_UNIT_SEP,
  ID_COMPONENT_RE,
} from "@/lib/api/types.generated";

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

// Not the generated `CycleHop`: this address is persisted in view memory and the hash, so a
// server-side field rename must not invalidate it.
interface PathHop {
  campaignId: string;
  cycleId: string;
}
export type CyclePath = PathHop[];

const HOP_SEP = CYCLE_PATH_HOP_SEP;

// All-dots ids match the charset but are traversal segments the server refuses.
// `lib/address.ts` validates through this too; never a second regex there.
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

// A sidebar node: an encoded path, optionally `|<candidate>`. The empty-path form is an origin,
// which groups several campaigns rather than addressing one.
const NODE_SEP = "|";

export function nodeAddress(path: CyclePath, nodeId?: string): string {
  const encoded = encodeCyclePath(path);
  return nodeId ? `${encoded}${NODE_SEP}${nodeId}` : encoded;
}

// The view-memory key (`lib/view-memory.tsx`): the root campaign, else the origin id. Never feed
// a suffixed address to `decodeCyclePath` — it answers null and the view memory goes inert.
export function ownerOfNodeAddress(addr: string): string | null {
  const cut = addr.indexOf(NODE_SEP);
  const encoded = cut < 0 ? addr : addr.slice(0, cut);
  if (!encoded) {
    const nodeId = cut < 0 ? "" : addr.slice(cut + NODE_SEP.length);
    return validIdComponent(nodeId) ? nodeId : null;
  }
  return decodeCyclePath(encoded)?.[0]?.campaignId ?? null;
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
