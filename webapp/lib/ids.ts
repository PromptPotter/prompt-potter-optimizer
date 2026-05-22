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
  if (m && m[2]) return `${m[1][0]}·${m[2]}`;
  return cycleId;
}

// Session ordinal from a root cycle id — `cycle_{hash}` → 1,
// `cycle_{hash}_s3` → 3. Mirrors `session_index()` in paths.py.
export function sessionIndexOf(rootId: string): number {
  const m = rootId.match(/_s(\d+)$/);
  return m ? Number(m[1]) : 1;
}

// Origin-hash tail of a campaign id — `{dataset}__{hash}` → the hash.
// Disambiguates two campaigns on the same dataset.
export function campaignOriginHash(campaignId: string): string {
  const sep = campaignId.indexOf("__");
  return sep >= 0 ? campaignId.slice(sep + 2) : campaignId;
}
