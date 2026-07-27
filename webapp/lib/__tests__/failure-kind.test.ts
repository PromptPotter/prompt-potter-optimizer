import { beforeEach, describe, expect, it } from "vitest";
import { ApiError, failureKind } from "@/lib/api";
import { clearIncidents, formatDiagnostics, getIncidents, reportIncident } from "@/lib/diagnostics";

// The classifier decides how a poll REACTS to a failure, so its direction of error
// matters more than its precision: mistaking transient for gone destroys the
// operator's view, while mistaking gone for transient only costs a retry. Every
// unmapped case must therefore land on `transient`.

describe("failureKind", () => {
  it("maps the statuses whose reactions differ", () => {
    expect(failureKind(new ApiError(401, "/api/v1/x"))).toBe("auth");
    expect(failureKind(new ApiError(403, "/api/v1/x"))).toBe("denied");
    expect(failureKind(new ApiError(404, "/api/v1/x"))).toBe("gone");
    expect(failureKind(new ApiError(400, "/api/v1/x"))).toBe("invalid");
    expect(failureKind(new ApiError(422, "/api/v1/x"))).toBe("invalid");
  });

  it("treats every server-side and unmapped failure as transient", () => {
    for (const status of [500, 502, 503, 504, 429, 418]) {
      expect(failureKind(new ApiError(status, "/api/v1/x"))).toBe("transient");
    }
  });

  it("treats a non-ApiError (network, parse, abort) as transient", () => {
    // A fetch that never reached the server proves nothing about the address.
    expect(failureKind(new TypeError("Failed to fetch"))).toBe("transient");
    expect(failureKind(new Error("Unexpected token < in JSON"))).toBe("transient");
    expect(failureKind(null)).toBe("transient");
    expect(failureKind(undefined)).toBe("transient");
  });

  it("carries the server's envelope fields so a report can name the cause", () => {
    const e = new ApiError(404, "/api/v1/campaigns/c/cycles/y/tree", "not_found", "abc123def456");
    expect(e.code).toBe("not_found");
    expect(e.errorId).toBe("abc123def456");
  });
});

describe("incident ring", () => {
  beforeEach(() => clearIncidents());

  it("records the trace handle and classification, not the payload", () => {
    reportIncident(new ApiError(404, "/api/v1/campaigns/c/cycles/y/tree?lens=x", "not_found", "id1"), {
      surface: "lineage",
      address: "c::y",
    });
    const [only] = getIncidents();
    expect(only?.errorId).toBe("id1");
    expect(only?.code).toBe("not_found");
    expect(only?.kind).toBe("gone");
    expect(only?.surface).toBe("lineage");
    // Query stripped — a lens or cursor is noise in a bug report.
    expect(only?.path).toBe("/api/v1/campaigns/c/cycles/y/tree");
  });

  it("collapses a repeating signature instead of letting it flood the ring", () => {
    // A stuck poll fires every 2 s. Without this, one dead address evicts the whole
    // history that would make the report legible.
    for (let i = 0; i < 30; i++) {
      reportIncident(new ApiError(404, "/api/v1/t", "not_found", `id${i}`), {
        surface: "lineage",
        address: "c::y",
      });
    }
    const ring = getIncidents();
    expect(ring).toHaveLength(1);
    expect(ring[0]?.count).toBe(30);
    // Latest id wins — it is the one nearest the operator's log tail.
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
