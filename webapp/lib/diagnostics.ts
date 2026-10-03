// The incident ring: each failure keyed by the `error_id` the API stamps, which greps the server
// log. Ids, codes, URLs and counts only — never measurements, prompt text or payloads.

import { useSyncExternalStore } from "react";
import { ApiError, failureKind, type FailureKind } from "@/lib/api";

export const DIAGNOSTICS_KEY = "promptpotter.diagnostics.incidents";

// Bumping DROPS the stored ring; never migrate it.
const RECORD_VERSION = 1;

const TTL_MS = 24 * 60 * 60 * 1000;

const MAX_INCIDENTS = 50;

export interface Incident {
  v: number;
  at: number;
  count: number;
  /** Null if the server never answered. */
  errorId: string | null;
  /** The `ErrorEnvelope` code (closed enum, api-openapi.yaml). */
  code: string | null;
  kind: FailureKind;
  status: number | null;
  path: string | null;
  surface: string;
  /** The encoded CyclePath, when the failure is address-scoped. */
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

function pathOf(url: string): string | null {
  try {
    return new URL(url, "http://x").pathname;
  } catch {
    return url || null;
  }
}

function signature(i: Incident): string {
  return `${i.surface}|${i.kind}|${i.code ?? i.status}|${i.address ?? i.path}`;
}

export interface IncidentContext {
  surface: string;
  address?: string | null;
}

// Aborts are not incidents: every poll aborts its predecessor.
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
      // The FIRST sighting's `at`, the LATEST id — its log line is nearest the tail.
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

/** The stable EMPTY identity, so first paint matches the static export. */
function emptyIncidents(): Ring {
  return EMPTY;
}

export function useIncidents(): Ring {
  return useSyncExternalStore(subscribeIncidents, getIncidents, emptyIncidents);
}

// Stable in shape. Never ship it anywhere from here: ADR-0004 keeps privileged outbound work
// out of the app.
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
