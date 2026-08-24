// THE BROWSER ADDRESS — one codec, one syntax, everything the URL says.
//
//   #/                                              following the active run, default view
//   #/dashboard                                     following, explicit view
//   #/c/<campaign>/<cycle>                          pinned
//   #/c/<campaign>/<cycle>/dashboard                pinned, explicit view
//   #/c/<campaign>/<cycle>/<campaign>/<cycle>       pinned to an inner run (one more hop)
//   #/c/<campaign>/<cycle>/dashboard/k/<candidate>  pinned, with a candidate parked
//   #/account/activity                              the account modal, on that pane
//
// It lives in the HASH and not the path because the app is `output: "export"` mounted by
// `StaticFiles(html=True)` at `/` — there is no SPA fallback, so `/c/<id>` would 404 on a
// reload. The hash never reaches the server, so every address above is one file.
//
// It replaced `?path=<campaign::cycle~campaign::cycle>&cand=<id>`. Two things were wrong
// with that: `URLSearchParams` percent-encodes the `::` separator, so the address a person
// copied was mostly `%3A%3A`; and the VIEW was not in it at all, so every reload dropped
// the operator back on Chat no matter what they had been reading.
//
// THE CYCLE PREFIX IS STRIPPED. Every cycle id begins `cycle_` — there are exactly two
// minters (`runner/campaign_ids.py::cycle_config_identity` and `::mint_checkin_cycle_id`)
// and the fork/diag/sweep separators are suffixes on top of it, so the prefix carries no
// information and costs six characters per hop. Stripping happens HERE and not in
// `lib/ids.ts`: `encodeCyclePath` there is also the wire format for the server's
// `?descend=` query and has to stay byte-exact.
//
// Ids are NOT percent-encoded on the way out, and do not need to be: every component is
// `validIdComponent` (letters, digits, `_`, `.`, `-`), which is a subset of the characters
// a URL fragment carries literally.

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

// Following the active run — the address names no cycle, only what to look at.
interface FollowAddress {
  kind: "follow";
  tab: Tab;
}
// Pinned to one cycle, at any depth, optionally with a candidate parked.
interface CycleAddress {
  kind: "cycle";
  path: CyclePath;
  tab: Tab;
  candidateId: string | null;
}
// The account modal. It does NOT carry the cycle: the pin lives in workspace state and
// is untouched while the modal is up, so closing it returns to where the operator was
// without a second memory of it.
interface AccountAddress {
  kind: "account";
  pane: AccountPane;
}
export type Address = FollowAddress | CycleAddress | AccountAddress;

const CYCLE_PREFIX = "cycle_";
// Marks the candidate that follows. A campaign id can never collide with it — see
// `parseAddress`.
const CAND_SEG = "k";
const CYCLE_SEG = "c";
const ACCOUNT_SEG = "account";

function shortCycle(cycleId: string): string {
  return cycleId.startsWith(CYCLE_PREFIX) ? cycleId.slice(CYCLE_PREFIX.length) : cycleId;
}

function longCycle(seg: string): string {
  return CYCLE_PREFIX + seg;
}

// What "following the active run, default view" formats to — the address that says
// nothing, which the writer in `workspace.tsx` renders as no hash at all.
export const EMPTY_ADDRESS = "#/";

export function formatAddress(a: Address): string {
  if (a.kind === "account") return `#/${ACCOUNT_SEG}/${a.pane}`;
  const segs: string[] = [];
  if (a.kind === "cycle") {
    segs.push(CYCLE_SEG);
    for (const hop of a.path) segs.push(hop.campaignId, shortCycle(hop.cycleId));
  }
  // The default view is OMITTED, so the common address stays short and `#/` is what
  // "following, nothing picked" looks like. `parseAddress` restores it.
  if (a.tab !== DEFAULT_TAB) segs.push(a.tab);
  if (a.kind === "cycle" && a.candidateId) segs.push(CAND_SEG, a.candidateId);
  return `#/${segs.join("/")}`;
}

// Null on anything malformed, so a caller falls back to following rather than crashing on
// a hand-edited hash. Never throws.
export function parseAddress(hash: string): Address | null {
  const segs = hash.replace(/^#/, "").split("/").filter(Boolean);
  if (segs.length === 0) return { kind: "follow", tab: DEFAULT_TAB };

  if (segs[0] === ACCOUNT_SEG) {
    if (segs.length > 2) return null;
    const pane = segs[1];
    if (pane === undefined) return { kind: "account", pane: DEFAULT_ACCOUNT_PANE };
    return isAccountPane(pane) ? { kind: "account", pane } : null;
  }

  if (segs[0] !== CYCLE_SEG) {
    // No cycle named — the whole address is the view.
    if (segs.length !== 1) return null;
    const tab = segs[0]!;
    return isTab(tab) ? { kind: "follow", tab } : null;
  }

  // Hops come in PAIRS after `c`, and the run of pairs ends at the first segment that is
  // a view name or `k`. Nothing can be both: a campaign id is always `{dataset}__{rand6}`
  // (`campaign_ids.py::mint_campaign_id`), so it always contains `__` and can never equal
  // a one-word view name — and only the FIRST of each pair is ever tested here, so a
  // cycle id is never asked the question at all.
  const path: CyclePath = [];
  let i = 1;
  while (i < segs.length) {
    const head = segs[i]!;
    if (isTab(head) || head === CAND_SEG) break;
    const cycle = segs[i + 1];
    if (cycle === undefined) return null; // a hop missing its cycle is not an address
    // Validate what was WRITTEN, before restoring the prefix — restoring first hands the
    // guard a string nobody typed, and `..` then passes as `cycle_..`: the all-dots
    // rejection stops matching the moment a prefix is glued in front of it. The prefix is
    // safe characters, so a valid segment can only make a valid id.
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
  if (i !== segs.length) return null; // trailing junk is malformed, not ignorable

  return { kind: "cycle", path, tab, candidateId };
}
