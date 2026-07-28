// Cycle-id helpers shared across the webapp.
//
// The Python side has `promptpotter.infrastructure.store.paths.root_cycle_id`
// + `sibling_kind` for the same job. We mirror just enough here to avoid
// round-tripping for every sidebar / lineage render — the regex is the same
// one paths.py uses.

const SIBLING_LAST_SEP_RE = /_(fork|diag|sweep)_(?!.*_(?:fork|diag|sweep)_)([^/]*)$/;
const SIBLING_FIRST_SEP_RE = /_(fork|diag|sweep)_/;

// Family-root id for a sibling, or the id itself when already a root.
// Mirrors `root_cycle_id()` in paths.py — uses the FIRST separator so
// `cycle_X_fork_Y_sweep_Z` still roots at `cycle_X`.
export function rootCycleId(cycleId: string): string {
  const m = cycleId.match(SIBLING_FIRST_SEP_RE);
  return m && m.index !== undefined ? cycleId.slice(0, m.index) : cycleId;
}

// Short "kind·tail" label for sibling rows in dense lists. Falls back to
// the full id when the parse fails (e.g. a root passed in by mistake).
export function shortFamilyTail(cycleId: string): string {
  const m = cycleId.match(SIBLING_LAST_SEP_RE);
  const kind = m?.[1];
  const tail = m?.[2];
  if (kind && tail) return `${kind.charAt(0)}·${tail}`;
  return cycleId;
}

// Composite unit key — a cycle_id is unique only within its campaign, so
// pickers key/value/store the pair `{campaign}::{cycle}`. Split it back on
// `UNIT_SEP`.
export const UNIT_SEP = "::";

export function unitKey(campaignId: string, cycleId: string): string {
  return `${campaignId}${UNIT_SEP}${cycleId}`;
}

// A cycle PATH — the single address for any cycle the webapp can view, inner or
// outer. An inner cycle (an L4 `promptpotter-self` fan-out campaign) is a normal
// cycle whose files live in a sandbox rooted at its parent, so it is addressed by
// the chain of `(campaign, cycle)` hops from the top-level root down to the leaf:
//   top-level  → [{c, cy}]
//   inner      → [{outerC, outerCy}, {innerC, innerCy}]
//   L5+        → deeper still (the engine's `.inner/<cycle_id>` sandbox is
//                re-entrant — this mirrors it, "never a depth-1 assumption").
// The dashboard reads the LEAF hop; chat/selection/dataset read the ROOT hop.
interface CycleHop {
  campaignId: string;
  cycleId: string;
}
// Non-empty by construction — every real address has at least the root hop.
export type CyclePath = CycleHop[];

// Hops are joined by `HOP_SEP`; each hop's ids by `UNIT_SEP`. Neither separator
// can occur inside an id (ids are `_SAFE_PATH_RE = ^[a-zA-Z0-9_.-]+$` on the
// Python side, `validate_path_component`), so encode/decode round-trips exactly.
const HOP_SEP = "~";
const ID_RE = /^[a-zA-Z0-9_.-]+$/;

export function encodeCyclePath(path: CyclePath): string {
  return path.map((h) => unitKey(h.campaignId, h.cycleId)).join(HOP_SEP);
}

// Parse an encoded path back to hops, validating every component; null on any
// malformed input so callers fall back to a safe default rather than crash.
export function decodeCyclePath(s: string): CyclePath | null {
  if (!s) return null;
  const hops: CyclePath = [];
  for (const seg of s.split(HOP_SEP)) {
    const i = seg.indexOf(UNIT_SEP);
    if (i < 0) return null;
    const campaignId = seg.slice(0, i);
    const cycleId = seg.slice(i + UNIT_SEP.length);
    if (!ID_RE.test(campaignId) || !ID_RE.test(cycleId)) return null;
    hops.push({ campaignId, cycleId });
  }
  return hops.length ? hops : null;
}

// A NODE's address in the sidebar tree: an encoded path, optionally suffixed with the id of
// something INSIDE that course — a candidate. Both halves are optional, and the empty-path
// form is real: an origin groups the runs of one declaration, which is a set of campaigns
// rather than an address in any of them.
const NODE_SEP = "|";

export function nodeAddress(path: CyclePath, nodeId?: string): string {
  const encoded = encodeCyclePath(path);
  return nodeId ? `${encoded}${NODE_SEP}${nodeId}` : encoded;
}

// Who OWNS a node address — the key its view memory files under (`lib/view-memory.tsx`).
// The root hop's campaign when the address names one, because that campaign's sidebar row
// owns the whole subtree; otherwise the id itself, which is the origin case (`cycle_<hash>`
// vs a campaign's `{dataset}__{rand6}` — the two key spaces cannot collide).
//
// This has to read the SUFFIX FORM, and that is the whole point of it existing: feeding a
// candidate's address to `decodeCyclePath` puts `|<id>` inside the cycle_id, fails `ID_RE`,
// and answers null — which read as "no campaign", so every candidate and origin twist went
// inert and stuck at its default. Silent: the button renders, the click does nothing.
export function ownerOfNodeAddress(addr: string): string | null {
  const cut = addr.indexOf(NODE_SEP);
  const encoded = cut < 0 ? addr : addr.slice(0, cut);
  if (!encoded) {
    const nodeId = cut < 0 ? "" : addr.slice(cut + NODE_SEP.length);
    return ID_RE.test(nodeId) ? nodeId : null;
  }
  return decodeCyclePath(encoded)?.[0]?.campaignId ?? null;
}

// The root hop — what chat, dataset, and files bind to (the top-level cycle that
// owns the operator conversation).
export function pathRoot(path: CyclePath): CycleHop {
  return path[0]!;
}

// The leaf hop — what the dashboard stream and the selection axes re-root to (the
// inner cycle when drilled in, else the root).
export function pathLeaf(path: CyclePath): CycleHop {
  return path[path.length - 1]!;
}

// The hops BELOW the root, HOP_SEP-joined — the `?descend=` query the dashboard
// route walks into `.inner/<previous cycle id>`. Empty string at depth 1, so a
// top-level request stays byte-identical to a plain per-cycle fetch.
export function encodeDescend(path: CyclePath): string {
  return encodeCyclePath(path.slice(1));
}
