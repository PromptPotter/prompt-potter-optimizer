// The incident ring — what turns "it broke" into something reportable.
//
// A user hit a real bug (the webapp pinned to a deleted campaign, polling 404s
// forever while telling them the SERVER was down) and it left no trace: no id,
// no record, nothing to carry into a bug report. The only artifact was a
// hand-pasted screenshot of a server log. This is the fix for that half — every
// read-path failure lands here, classified, with the `error_id` the API stamped
// on the envelope (`main.py::_error_response`).
//
// That id is the whole point. It appears in three places at once — the response
// body, the server log line, and this ring — so a report quotes it and the
// operator greps straight to the cause instead of guessing at a wall-clock
// window (frontend-surface-contract.md § I7_failure_traceable).
//
// NOT a React context. A catch block in a poll tick, a registry callback or a
// non-component helper must all be able to report, so the store is a plain
// module with `useSyncExternalStore` for the one surface that reads it. Same
// shape as `lib/lineage-registry.ts`.
//
// WHAT IS NEVER RECORDED: measurements, prompt text, payload bodies, tokens.
// Only ids, codes, URLs and counts — the same hard rule `lib/view-memory.tsx`
// states for its record, and for the same reason: this blob is meant to be
// pasted into an issue tracker by a human who should not have to audit it first.

import { useSyncExternalStore } from "react";
import { ApiError, failureKind, type FailureKind } from "@/lib/api";

export const DIAGNOSTICS_KEY = "promptpotter.diagnostics.incidents";

// Bumping DROPS the stored ring rather than migrating it — a lost diagnostic
// costs nothing, and a migration path for debug breadcrumbs is more code than
// the thing it protects (`view-memory.tsx` makes the same trade).
const RECORD_VERSION = 1;

// Long enough to survive the reload the bug provokes and a coffee break; short
// enough that a report never carries last month's unrelated noise.
const TTL_MS = 24 * 60 * 60 * 1000;

// Newest-first, bounded. A stuck poll can fire every 2 s, so the cap is what
// stops one runaway surface evicting the diverse history that makes a report
// legible — see `record`, which dedupes a repeating signature instead of
// letting it fill the ring.
const MAX_INCIDENTS = 50;

export interface Incident {
  v: number;
  at: number; // epoch ms, first sighting
  /** Repeats of the same signature collapse into one row and bump this. */
  count: number;
  /** The server's trace handle — greps to the log line. Null if it never answered. */
  errorId: string | null;
  /** The `ErrorEnvelope` code (closed enum, api-openapi.yaml). */
  code: string | null;
  kind: FailureKind;
  status: number | null;
  /** Path only — a query string can carry a lens or a cursor nobody needs. */
  path: string | null;
  /** Which client surface was asking, e.g. "lineage" / "dashboard" / "ray". */
  surface: string;
  /** The encoded CyclePath being read, when the failure is address-scoped. */
  address: string | null;
}

type Ring = Incident[];

const EMPTY: Ring = [];

let cache: Ring | null = null;
const listeners = new Set<() => void>();

function prune(ring: Ring, now: number): Ring {
  return ring
    .filter((i) => i && i.v === RECORD_VERSION && now - i.at < TTL_MS)
    .slice(0, MAX_INCIDENTS);
}

function read(): Ring {
  if (cache) return cache;
  if (typeof window === "undefined") return EMPTY;
  try {
    const raw = window.localStorage.getItem(DIAGNOSTICS_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    cache = Array.isArray(parsed) ? prune(parsed as Ring, Date.now()) : EMPTY;
  } catch {
    cache = EMPTY; // private mode, quota, corrupt blob — diagnostics never throw
  }
  return cache;
}

function write(ring: Ring): void {
  cache = ring;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(DIAGNOSTICS_KEY, JSON.stringify(ring));
    } catch {
      /* nothing persists; the in-memory ring still serves this session */
    }
  }
  for (const l of listeners) l();
}

/** Strip the query — it carries lens/cursor noise and can be long. */
function pathOf(url: string): string | null {
  try {
    return new URL(url, "http://x").pathname;
  } catch {
    return url || null;
  }
}

// Two failures are "the same" when the same surface hit the same code on the
// same address. That is the grain a reader reasons about ("the tree 404s"), and
// collapsing it is what keeps a 2 s poll from evicting everything else.
function signature(i: Incident): string {
  return `${i.surface}|${i.kind}|${i.code ?? i.status}|${i.address ?? i.path}`;
}

export interface IncidentContext {
  surface: string;
  address?: string | null;
}

/**
 * Record a failure. Safe to call from any catch — it never throws and never
 * awaits, so a reporting bug can't become the incident.
 *
 * Aborts are not incidents: a cancelled in-flight request is this app working
 * correctly (every poll aborts its predecessor), and recording them would bury
 * the real signal.
 */
export function reportIncident(e: unknown, ctx: IncidentContext): void {
  if (e instanceof DOMException && e.name === "AbortError") return;
  if (e instanceof Error && e.name === "AbortError") return;

  const api = e instanceof ApiError ? e : null;
  const now = Date.now();
  const next: Incident = {
    v: RECORD_VERSION,
    at: now,
    count: 1,
    errorId: api?.errorId ?? null,
    code: api?.code ?? null,
    kind: failureKind(e),
    status: api?.status ?? null,
    path: api ? pathOf(api.url) : null,
    surface: ctx.surface,
    address: ctx.address ?? null,
  };

  const ring = read();
  const sig = signature(next);
  const hitIndex = ring.findIndex((i) => signature(i) === sig);
  if (hitIndex >= 0) {
    const hit = ring[hitIndex];
    if (hit) {
      // Keep the FIRST sighting's `at` (when it started) but the LATEST id —
      // that is the one whose log line is still nearest the operator's tail.
      const merged: Incident = {
        ...hit,
        count: hit.count + 1,
        errorId: next.errorId ?? hit.errorId,
      };
      write([merged, ...ring.slice(0, hitIndex), ...ring.slice(hitIndex + 1)]);
      return;
    }
  }
  write(prune([next, ...ring], now));
}

export function getIncidents(): Ring {
  return read();
}

function subscribeIncidents(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function clearIncidents(): void {
  write(EMPTY);
}

/** Server snapshot — the stable EMPTY identity, so first paint matches the export. */
function emptyIncidents(): Ring {
  return EMPTY;
}

/** Live view of the ring for the one surface that renders it. */
export function useIncidents(): Ring {
  return useSyncExternalStore(subscribeIncidents, getIncidents, emptyIncidents);
}

/**
 * The paste-ready report. Markdown because its destination is an issue tracker,
 * and a stable shape because something downstream may parse it — the repo's job
 * ends at producing this; shipping it anywhere is a deployment concern (ADR-0004
 * keeps privileged outbound work in a companion process, never in the app).
 */
export function formatDiagnostics(opts: { version: string | null }): string {
  const ring = getIncidents();
  const lines = [
    "### PromptPotter diagnostics",
    "",
    `- app version: ${opts.version ?? "unknown"}`,
    `- captured: ${new Date().toISOString()}`,
    `- incidents: ${ring.length}`,
    "",
  ];
  if (ring.length === 0) {
    lines.push("No failures recorded in the last 24 hours.");
    return lines.join("\n");
  }
  lines.push(
    "| when | surface | kind | code | status | error_id | address |",
    "|---|---|---|---|---|---|---|",
  );
  for (const i of ring) {
    const when = new Date(i.at).toISOString() + (i.count > 1 ? ` (×${i.count})` : "");
    lines.push(
      `| ${when} | ${i.surface} | ${i.kind} | ${i.code ?? "—"} | ${i.status ?? "—"} | ` +
        `${i.errorId ?? "—"} | ${i.address ?? i.path ?? "—"} |`,
    );
  }
  lines.push(
    "",
    "`error_id` greps the server log: `api error [<id>] <method> <path> -> <status> <code>`.",
  );
  return lines.join("\n");
}
