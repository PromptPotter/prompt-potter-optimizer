import { beforeEach, describe, expect, it } from "vitest";
import { ApiError, failureKind, IngestApiError, operatorMessage } from "@/lib/api";
import { clearIncidents, formatDiagnostics, getIncidents, reportIncident } from "@/lib/diagnostics";

// Every unmapped case lands on `transient`: mistaking transient for gone destroys the
// operator's view, while the reverse costs one retry.

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

  it("classifies a WRITE failure by its status, like any other", () => {
    // Command and ingest writes throw `IngestApiError`; it must classify inside this family.
    const write = (status: number) =>
      failureKind(new IngestApiError(status, "/api/v1/commands/compact-archive", "no"));
    expect(write(403)).toBe("denied");
    expect(write(422)).toBe("invalid");
    expect(write(500)).toBe("transient");
  });

  it("words a write failure as the server's sentence, else one per kind — never the raw failure", () => {
    const said = new IngestApiError(409, "/api/v1/commands/fork-cycle", "Already forked.");
    expect(operatorMessage(said, failureKind(said))).toBe("Already forked.");
    // No envelope (a proxy 502) and no response at all: neither the status line nor the
    // browser's network text reaches the operator, and neither claims the write did not land.
    const bare = new IngestApiError(502, "/api/v1/commands/fork-cycle", null);
    const offline = new TypeError("Failed to fetch");
    for (const e of [bare, offline]) {
      const text = operatorMessage(e, failureKind(e));
      expect(text).not.toMatch(/502|fetch/i);
      expect(text).toMatch(/may not have been applied/);
    }
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
    expect(only?.path).toBe("/api/v1/campaigns/c/cycles/y/tree");
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

  it("records a write failure's trace handle, not a blank row", () => {
    reportIncident(
      new IngestApiError(500, "/api/v1/commands/start-run", "boom", "internal_error", "wid7"),
      { surface: "run-control" },
    );
    const [only] = getIncidents();
    expect(only?.errorId).toBe("wid7");
    expect(only?.code).toBe("internal_error");
    expect(only?.status).toBe(500);
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
