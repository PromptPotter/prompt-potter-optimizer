import { beforeEach, describe, expect, it } from "vitest";
import {
  ApiError,
  commandMessage,
  failureKind,
  failurePasses,
  operatorMessage,
  type FailureKind,
} from "@/lib/api";
import { toApiError } from "@/lib/api/client";
import { clearIncidents, formatDiagnostics, getIncidents, reportIncident } from "@/lib/diagnostics";
import { connectionSaid } from "@/lib/poll";

describe("failureKind", () => {
  it("maps the statuses whose reactions differ", () => {
    expect(failureKind(new ApiError(401, "/api/v1/x"))).toBe("auth");
    expect(failureKind(new ApiError(403, "/api/v1/x"))).toBe("denied");
    expect(failureKind(new ApiError(404, "/api/v1/x"))).toBe("gone");
    expect(failureKind(new ApiError(400, "/api/v1/x"))).toBe("invalid");
    expect(failureKind(new ApiError(422, "/api/v1/x"))).toBe("invalid");
    // An answered refusal is the read failing, never the server unreachable.
    const auth = new ApiError(401, "/api/v1/x");
    const kind = failureKind(auth);
    const message = operatorMessage(auth, kind);
    const refused = connectionSaid({ kind, message }, "followed");
    const offline = connectionSaid({ kind: "transient", message }, "followed");
    expect(refused?.label).toBe(message);
    expect(refused?.label).not.toBe(offline?.label);
  });

  it("treats every server-side failure as transient", () => {
    for (const status of [500, 502, 503, 504]) {
      expect(failureKind(new ApiError(status, "/api/v1/x"))).toBe("transient");
    }
  });

  it("reads any other answered 4xx as a refusal, worded by the server, read or write", () => {
    for (const status of [409, 418]) {
      expect(failureKind(new ApiError(status, "/api/v1/x"))).toBe("refused");
    }
    const capped = new ApiError(409, "/api/v1/x", "spend_cap", null, null, "Raise the cap to go on.");
    expect(operatorMessage(capped, failureKind(capped))).toBe("Raise the cap to go on.");
    const bare = new ApiError(409, "/api/v1/x");
    expect(operatorMessage(bare, failureKind(bare))).not.toMatch(/reach/i);
  });

  it("reads the request-rate throttle as passing, and a throttled write as never applied", () => {
    const slow = new ApiError(429, "/api/v1/commands/x", "rate_limited", null, null, "Slow down.");
    expect(failureKind(slow)).toBe("throttled");
    expect(commandMessage(slow, failureKind(slow))).toBe("Slow down.");
  });

  it("names the failures that pass, and the connection row says such a read is asked again", () => {
    // A 404 passes for no reader: the cache alone re-asks it, until its gone verdict.
    const passes: Record<FailureKind, boolean> = {
      transient: true,
      throttled: true,
      gone: false,
      auth: false,
      denied: false,
      invalid: false,
      refused: false,
    };
    for (const [kind, expected] of Object.entries(passes) as [FailureKind, boolean][]) {
      expect(failurePasses(kind)).toBe(expected);
    }
    const throttled = connectionSaid({ kind: "throttled", message: "Slow down." }, "followed");
    expect(throttled?.label).toBe("Slow down.");
    expect(throttled?.detail).not.toBeNull();
    expect(connectionSaid({ kind: "denied", message: "Not yours." }, "followed")?.detail).toBeNull();
    // Off any read but the viewed cycle's, a 404 is its own sentence, never a gone campaign.
    const missing = connectionSaid({ kind: "gone", message: "No such route." }, "listing");
    expect(missing).toMatchObject({ key: "connection_lost", label: "No such route.", detail: null });
  });

  it("says a gone viewed campaign returns to the active run off a pin alone", () => {
    const gone = { kind: "gone" as const, message: "Not found." };
    const returning = /Returning to the active run/;
    expect(connectionSaid(gone, "pinned")?.detail).toMatch(returning);
    expect(connectionSaid(null, "gone")?.detail).toMatch(returning);
    const followed = connectionSaid(gone, "followed");
    expect(followed?.key).toBe("address_gone");
    expect(followed?.detail).not.toMatch(returning);
  });

  it("treats a non-ApiError (network, parse, abort) as transient", () => {
    expect(failureKind(new TypeError("Failed to fetch"))).toBe("transient");
    expect(failureKind(new Error("Unexpected token < in JSON"))).toBe("transient");
    expect(failureKind(null)).toBe("transient");
    expect(failureKind(undefined)).toBe("transient");
  });

  it("words a write failure as the server's sentence, else one per kind — never the raw failure", () => {
    const said = new ApiError(409, "/api/v1/commands/fork-cycle", null, null, null, "Already forked.");
    expect(commandMessage(said, failureKind(said))).toBe("Already forked.");
    // No envelope (a proxy 502) and no response at all: neither may claim the write did not land,
    // and a read that failed the same way claims nothing about writes.
    const bare = new ApiError(502, "/api/v1/commands/fork-cycle");
    const offline = new TypeError("Failed to fetch");
    for (const e of [bare, offline]) {
      const text = commandMessage(e, failureKind(e));
      expect(text).not.toMatch(/502|fetch/i);
      expect(text).toMatch(/may not have been applied/);
      expect(operatorMessage(e, failureKind(e))).not.toMatch(/applied/);
    }
  });

  it("carries the server's envelope fields so a report can name the cause", () => {
    const e = new ApiError(404, "/api/v1/hierarchy/c", "not_found", "abc123def456");
    expect(e.code).toBe("not_found");
    expect(e.errorId).toBe("abc123def456");
  });
});

describe("incident ring", () => {
  beforeEach(() => clearIncidents());

  it("records the trace handle and classification, not the payload", () => {
    reportIncident(new ApiError(404, "/api/v1/hierarchy/c?lens=x", "not_found", "id1"), {
      surface: "lineage",
      address: "c::y",
    });
    const [only] = getIncidents();
    expect(only?.errorId).toBe("id1");
    expect(only?.code).toBe("not_found");
    expect(only?.kind).toBe("gone");
    expect(only?.surface).toBe("lineage");
    expect(only?.path).toBe("/api/v1/hierarchy/c");
  });

  it("collapses a repeating signature instead of letting it flood the ring", () => {
    for (let i = 0; i < 30; i++) {
      reportIncident(new ApiError(404, "/api/v1/t", "not_found", `id${i}`), {
        surface: "lineage",
        address: "c::y",
      });
    }
    const ring = getIncidents();
    expect(ring).toHaveLength(1);
    expect(ring[0]?.count).toBe(30);
    expect(ring[0]?.errorId).toBe("id29");
  });

  it("keeps distinct signatures apart and bounds the ring", () => {
    for (let i = 0; i < 80; i++) {
      reportIncident(new ApiError(500, `/api/v1/${i}`, "internal_error", `id${i}`), {
        surface: `s${i}`,
      });
    }
    expect(getIncidents().length).toBeLessThanOrEqual(50);
  });

  it("records a write failure's trace handle off its envelope, not a blank row", async () => {
    const envelope = { code: "internal_error", message: "", error_id: "wid7", details: {} };
    const e = await toApiError(new Response(JSON.stringify(envelope), { status: 500 }));
    reportIncident(e, { surface: "run-control" });
    const [only] = getIncidents();
    expect(only?.errorId).toBe("wid7");
    expect(only?.code).toBe("internal_error");
    expect(only?.status).toBe(500);
    expect(only?.path).toBeNull();
    expect(commandMessage(e, failureKind(e))).toMatch(/may not have been applied/);
  });

  it("ignores aborts — a cancelled in-flight request is the app working correctly", () => {
    const abort = new Error("The operation was aborted");
    abort.name = "AbortError";
    reportIncident(abort, { surface: "dashboard" });
    expect(getIncidents()).toHaveLength(0);
  });

  it("formats a report that names the id and how to use it", () => {
    reportIncident(new ApiError(404, "/api/v1/t", "not_found", "deadbeef"), {
      surface: "dashboard",
      address: "c::y",
    });
    const md = formatDiagnostics({ version: "0.8.8" });
    expect(md).toContain("deadbeef");
    expect(md).toContain("0.8.8");
    expect(md).toContain("error_id");
  });

  it("says so plainly when there is nothing to report", () => {
    expect(formatDiagnostics({ version: null })).toContain("No failures recorded");
  });
});
