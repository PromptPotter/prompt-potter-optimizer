// The hash: the viewed subject in `domain/cycle_paths.py::Subject`'s hierarchy grammar
// (`ids.ts::encodeCyclePath`, then `/r{round}/{arm}`), the open views, then the view keys as `;name=value`.

import { CYCLE_PATH_UNIT_SEP } from "@/lib/api/types";
import { decodeCyclePath, encodeCyclePath, validIdComponent, type CyclePath } from "./ids";

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

// One open view family; `key` picks what it shows (a cell's archive answer, a node, an account tab).
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

// A candidate picked on a course: its place below the course, the served address's `r{round}/{arm_key}` tail.
export interface CandidatePick {
  round: number;
  arm: string;
}

export function formatPick({ round, arm }: CandidatePick): string {
  return `r${round}/${arm}`;
}

export function parsePick(s: string): CandidatePick | null {
  const m = /^r(\d+)\/(.+)$/.exec(s);
  return m && validIdComponent(m[2]!) ? { round: Number(m[1]), arm: m[2]! } : null;
}

// The picked candidate's hierarchy address under its course's.
export function pickedAddressOf(course: string, pick: CandidatePick): string {
  return `${course}/${formatPick(pick)}`;
}

// null follows the active run; a check-in's null draft is a fresh one no file or pick has minted.
export type ViewedSubject =
  | { path: CyclePath; pick?: CandidatePick | null; at?: number | null }
  | { draftId: string | null }
  | null;

// What rides beside the subject on the viewed leaf; a subject switch clears them.
// The `;observe=` key, in the order its picker offers it. The Leader shows the leader: the pick
// renders in the inspector alone (`ScoringInspector`).
export const OBSERVE_STATES = ["best", "latest", "round"] as const;
export type ObserveState = (typeof OBSERVE_STATES)[number];

export interface ViewKeys {
  // null follows live; a pick reads its own round.
  round: number | null;
  // `null` is no pick (the served set); `[]` is the picker open with nothing selected.
  samples: number[] | null;
  // `null` follows `resolveObserveSubject`'s own rule.
  observe: ObserveState | null;
}

export const NO_KEYS: ViewKeys = { round: null, samples: null, observe: null };

export interface Address {
  subject: ViewedSubject;
  // Never empty: the order is the order opened, the newest the ones a screen shows.
  views: readonly View[];
  keys: ViewKeys;
}

// Why a hash opens nothing: a link in the grammar before this one (`#/c/{campaign}/{cycle}…`),
// or one in no grammar this version reads.
export type AddressRefusal = "retired" | "malformed";

export interface RefusedAddress {
  refused: AddressRefusal;
}

const MALFORMED: RefusedAddress = { refused: "malformed" };
// The cycle segment of the grammar before the hierarchy's.
const RETIRED_CYCLE_SEG = "c";
const DRAFT_SEG = "d";
const NEW_SEG = "new";
const KEY_SEP = "=";

export const DEFAULT_VIEWS: readonly View[] = [{ family: "chat", key: null }];

export function withViews(views: readonly View[]): readonly View[] {
  return views.length > 0 ? views : DEFAULT_VIEWS;
}

// `workspace.tsx` writes this address as no hash at all.
export const EMPTY_ADDRESS = "#/";

export function formatAddress({ subject, views, keys }: Address): string {
  const segs: string[] = [];
  const named: string[] = [];
  if (subject !== null && "draftId" in subject) {
    segs.push(...(subject.draftId === null ? [NEW_SEG] : [DRAFT_SEG, subject.draftId]));
  } else if (subject !== null) {
    const course = encodeCyclePath(subject.path);
    segs.push(subject.pick ? pickedAddressOf(course, subject.pick) : course);
    if (subject.at != null) named.push(`at=${subject.at}`);
  }
  // The default view is omitted, so the common address stays short.
  const bare = views.length === 1 && views[0]!.family === "chat" && views[0]!.key === null;
  if (!bare) for (const v of views) segs.push(v.key === null ? v.family : `${v.family}${KEY_SEP}${v.key}`);
  if (keys.round !== null) named.push(`round=${keys.round}`);
  if (keys.samples !== null) named.push(`samples=${keys.samples.join(",")}`);
  if (keys.observe !== null) named.push(`observe=${keys.observe}`);
  return `#/${[segs.join("/"), ...named].join(";")}`;
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

const COUNT_RE = /^\d+$/;

// Takes the subject's segments off the front of *segs*; undefined is a malformed subject.
function takeSubject(segs: string[]): ViewedSubject | undefined {
  const head = segs[0];
  if (head === NEW_SEG) {
    segs.shift();
    return { draftId: null };
  }
  if (head === DRAFT_SEG) {
    const draftId = segs[1];
    segs.splice(0, 2);
    return draftId !== undefined && validIdComponent(draftId) ? { draftId } : undefined;
  }
  // Only a hop holds the unit separator, so a view or a typo never reads as a course.
  if (head === undefined || !head.includes(CYCLE_PATH_UNIT_SEP)) return null;
  const path = decodeCyclePath(head);
  segs.shift();
  if (!path) return undefined;
  if (!/^r\d+$/.test(segs[0] ?? "")) return { path, pick: null, at: null };
  const pick = parsePick(segs.slice(0, 2).join("/"));
  segs.splice(0, 2);
  return pick ? { path, pick, at: null } : undefined;
}

export function parseAddress(hash: string): Address | RefusedAddress {
  const [head = "", ...named] = hash.replace(/^#\/?/, "").split(";");
  const segs = head.split("/").filter(Boolean);
  if (segs[0] === RETIRED_CYCLE_SEG) return { refused: "retired" };
  const subject = takeSubject(segs);
  if (subject === undefined) return MALFORMED;
  const views: View[] = [];
  for (const seg of segs) {
    const view = parseView(seg);
    // Trailing junk is not ignorable, and one family never shows twice.
    if (!view || views.some((v) => v.family === view.family)) return MALFORMED;
    views.push(view);
  }

  const keys: ViewKeys = { ...NO_KEYS };
  let at: number | null = null;
  const seen = new Set<string>();
  for (const seg of named) {
    const eq = seg.indexOf(KEY_SEP);
    const name = seg.slice(0, eq);
    const value = seg.slice(eq + 1);
    if (eq < 0 || seen.has(name)) return MALFORMED;
    seen.add(name);
    if (name === "at" && COUNT_RE.test(value) && subject !== null && "path" in subject) at = Number(value);
    else if (name === "round" && COUNT_RE.test(value)) keys.round = Number(value);
    else if (name === "observe" && (OBSERVE_STATES as readonly string[]).includes(value)) keys.observe = value as ObserveState;
    else if (name === "samples" && /^(\d+(,\d+)*)?$/.test(value)) keys.samples = value ? value.split(",").map(Number) : [];
    else return MALFORMED;
  }
  return {
    subject: subject !== null && "path" in subject ? { ...subject, at } : subject,
    views: withViews(views),
    keys,
  };
}
