// The `cycle_` prefix is stripped HERE, never in `ids.ts`, whose encoding is the `?descend=` wire.

import { validIdComponent, type CyclePath } from "./ids";

// A client-side closed set on purpose: no route names a view. `components/shell/families.tsx` labels each.
export const FAMILY_IDS = [
  "chat",
  "timeline",
  "leader",
  "candidates",
  "spend",
  "heatmap",
  "samples",
  "optimizer",
  "node",
  "files",
  "compare",
  "account",
  "campaigns",
] as const;
export type FamilyId = (typeof FAMILY_IDS)[number];

// One open view family; `key` picks what it shows (a cell's archive answer, a node, an account section).
export interface View {
  family: FamilyId;
  key: string | null;
}

// A node id alone is ambiguous: `promptpotter-self`'s TARGET pipeline reuses the OPTIMIZER's node ids.
export type NodeScope = "optimizer" | "target";

export interface SelectedNode {
  id: string;
  scope: NodeScope;
}

// The `node` view's key: `<scope>.<node id>`.
export function nodeKey({ scope, id }: SelectedNode): string {
  return `${scope}.${id}`;
}

export function nodeOfKey(key: string): SelectedNode | null {
  const dot = key.indexOf(".");
  if (dot < 0) return null;
  const scope = key.slice(0, dot);
  const id = key.slice(dot + 1);
  return (scope === "optimizer" || scope === "target") && id ? { scope, id } : null;
}

// null follows the active run; a check-in's null draft is a fresh one no file or pick has minted.
export type ViewedSubject =
  | { path: CyclePath; candidateId?: string | null; at?: number | null }
  | { draftId: string | null }
  | null;

export interface Address {
  subject: ViewedSubject;
  // Never empty: the order is the order opened, the newest the ones a screen shows.
  views: readonly View[];
}

const CYCLE_PREFIX = "cycle_";
const CAND_SEG = "k";
const AT_SEG = "t";
const CYCLE_SEG = "c";
const DRAFT_SEG = "d";
const NEW_SEG = "new";
const KEY_SEP = "=";

export const DEFAULT_VIEWS: readonly View[] = [{ family: "chat", key: null }];

export function withViews(views: readonly View[]): readonly View[] {
  return views.length > 0 ? views : DEFAULT_VIEWS;
}

function shortCycle(cycleId: string): string {
  return cycleId.startsWith(CYCLE_PREFIX) ? cycleId.slice(CYCLE_PREFIX.length) : cycleId;
}

// `workspace.tsx` writes this address as no hash at all.
export const EMPTY_ADDRESS = "#/";

export function formatAddress({ subject, views }: Address): string {
  const segs: string[] = [];
  if (subject !== null && "draftId" in subject) {
    segs.push(...(subject.draftId === null ? [NEW_SEG] : [DRAFT_SEG, subject.draftId]));
  } else if (subject !== null) {
    segs.push(CYCLE_SEG);
    for (const hop of subject.path) segs.push(hop.campaignId, shortCycle(hop.cycleId));
    if (subject.candidateId) segs.push(CAND_SEG, subject.candidateId);
    if (subject.at != null) segs.push(AT_SEG, String(subject.at));
  }
  // The default view is omitted, so the common address stays short.
  const bare = views.length === 1 && views[0]!.family === "chat" && views[0]!.key === null;
  if (!bare) for (const v of views) segs.push(v.key === null ? v.family : `${v.family}${KEY_SEP}${v.key}`);
  return `#/${segs.join("/")}`;
}

function isFamily(s: string): s is FamilyId {
  return (FAMILY_IDS as readonly string[]).includes(s);
}

function parseView(seg: string): View | null {
  const [family, key, extra] = seg.split(KEY_SEP);
  if (family === undefined || !isFamily(family) || extra !== undefined) return null;
  if (key === undefined) return { family, key: null };
  if (family === "node" && !nodeOfKey(key)) return null;
  return validIdComponent(key) ? { family, key } : null;
}

export function parseAddress(hash: string): Address | null {
  const segs = hash.replace(/^#/, "").split("/").filter(Boolean);
  let subject: ViewedSubject = null;
  let i = 0;

  if (segs[0] === NEW_SEG) {
    subject = { draftId: null };
    i = 1;
  } else if (segs[0] === DRAFT_SEG) {
    const draftId = segs[1];
    if (draftId === undefined || !validIdComponent(draftId)) return null;
    subject = { draftId };
    i = 2;
  } else if (segs[0] === CYCLE_SEG) {
    // A campaign id always contains `__` (`campaign_ids.py::mint_campaign_id`), so it never equals a view, `k` or `t`.
    const path: CyclePath = [];
    i = 1;
    while (i < segs.length) {
      const head = segs[i]!;
      if (head === CAND_SEG || head === AT_SEG || parseView(head)) break;
      const cycle = segs[i + 1];
      if (cycle === undefined) return null;
      // Validate BEFORE restoring the prefix: `cycle_..` passes the all-dots rejection that `..` fails.
      if (!validIdComponent(head) || !validIdComponent(cycle)) return null;
      path.push({ campaignId: head, cycleId: CYCLE_PREFIX + cycle });
      i += 2;
    }
    if (path.length === 0) return null;
    const run: { path: CyclePath; candidateId: string | null; at: number | null } = {
      path,
      candidateId: null,
      at: null,
    };
    if (segs[i] === CAND_SEG) {
      const id = segs[i + 1];
      if (id === undefined || !validIdComponent(id)) return null;
      run.candidateId = id;
      i += 2;
    }
    if (segs[i] === AT_SEG) {
      const offset = segs[i + 1];
      if (offset === undefined || !/^\d+$/.test(offset)) return null;
      run.at = Number(offset);
      i += 2;
    }
    subject = run;
  }

  const views: View[] = [];
  for (const seg of segs.slice(i)) {
    const view = parseView(seg);
    // Trailing junk is not ignorable, and one family never shows twice.
    if (!view || views.some((v) => v.family === view.family)) return null;
    views.push(view);
  }
  return { subject, views: withViews(views) };
}
