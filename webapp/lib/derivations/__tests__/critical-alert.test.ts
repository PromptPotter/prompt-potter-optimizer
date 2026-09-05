import { describe, expect, it } from "vitest";
import { criticalAlert } from "../critical-alert";
import type { DashboardSnapshot } from "@/lib/poll";

const base = {
  bannerStatus: "live" as const,
  bannerText: "Live · last write 2s ago",
  bannerHint: undefined,
  dash: null as DashboardSnapshot | null,
};

describe("criticalAlert", () => {
  it("returns null on a clean live run", () => {
    expect(criticalAlert(base)).toBeNull();
  });

  it("flags a crashed run as critical, regardless of connection", () => {
    const dash = {
      run_phase: "terminal",
      error: { kind: "CRASHED", message: "boom\nretry", stop_reason: "crashed" },
    } as DashboardSnapshot;
    expect(criticalAlert({ ...base, dash })).toEqual({
      severity: "critical",
      title: "Crashed — CRASHED",
      detail: "stop: crashed",
    });
  });

  // A DESIGNED refusal must not be announced as the run falling over. The engine halted on
  // purpose and named a recovery; calling it a crash sends the operator hunting a bug that
  // does not exist. The label comes from the generated STOP_REASON_LABELS, never from here.
  it("names a designed halt by its own stop reason rather than calling it a crash", () => {
    const dash = {
      run_phase: "terminal",
      error: {
        kind: "ResumeDivergenceError",
        message: "optimizer_identity:l1_generate",
        stop_reason: "diverged",
      },
    } as DashboardSnapshot;
    expect(criticalAlert({ ...base, dash })).toEqual({
      severity: "critical",
      title: "Diverged — ResumeDivergenceError",
      detail: "stop: diverged",
    });
  });

  // An unknown reason must still not read as a crash — the fallback names nothing it cannot
  // verify. A reason the generated table does not carry is newer than this build, not a crash.
  it("falls back to a neutral title for a stop reason the label table does not carry", () => {
    const dash = {
      run_phase: "terminal",
      error: { kind: "SomethingNew", message: "x", stop_reason: "not_in_table" },
    } as DashboardSnapshot;
    expect(criticalAlert({ ...base, dash })?.title).toBe("Run stopped — SomethingNew");
  });

  it("flags server-unreachable (offline) as critical with the hint as detail", () => {
    expect(
      criticalAlert({
        ...base,
        bannerStatus: "offline",
        bannerText: "Server unreachable — retrying",
        bannerHint: "Resume: python -m promptpotter resume",
      }),
    ).toEqual({
      severity: "critical",
      title: "Server unreachable — retrying",
      detail: "Resume: python -m promptpotter resume",
    });
  });

  it("stays silent for an empty workspace, despite the poll's resting offline", () => {
    // A first-run account: no cycle, so the poll never left INITIAL_STATE. Without
    // its own signal this is indistinguishable from an outage and paints red.
    expect(
      criticalAlert({
        ...base,
        bannerStatus: "offline",
        bannerText: "Connecting…",
        emptyWorkspace: true,
      }),
    ).toBeNull();
  });

  it("a genuinely unreachable server still wins over an empty workspace", () => {
    // The caller subtracts netDown before setting emptyWorkspace; this pins that
    // the derivation does not silence a real outage if that ever slips.
    const out = criticalAlert({
      ...base,
      bannerStatus: "offline",
      bannerText: "Server unreachable — retrying",
      emptyWorkspace: false,
    });
    expect(out?.title).toBe("Server unreachable — retrying");
  });

  it("flags a gone-silent run (server says running, this poll has gone stale) as a warning", () => {
    const dash = { run_phase: "running" } as DashboardSnapshot;
    expect(
      criticalAlert({
        ...base,
        dash,
        bannerStatus: "stale",
        bannerText: "Stale · last write 90s ago",
      }),
    ).toEqual({
      severity: "warn",
      title: "Run went silent",
      detail: "Stale · last write 90s ago",
    });
  });

  it("does not flag a clean terminal (no error record)", () => {
    const dash = { run_phase: "terminal", stop_reason: "target_reached" } as DashboardSnapshot;
    expect(criticalAlert({ ...base, dash })).toBeNull();
  });

  it("stays silent for warming_up (reachable, no snapshot yet)", () => {
    const out = criticalAlert({
      ...base,
      bannerStatus: "stale",
      bannerText: "Origin running",
    });
    expect(out).toBeNull();
  });

  it("a terminal error takes precedence over offline", () => {
    const dash = {
      run_phase: "terminal",
      error: { kind: "DIVERGED", message: "x", stop_reason: "diverged" },
    } as DashboardSnapshot;
    const out = criticalAlert({ ...base, bannerStatus: "offline", dash });
    expect(out?.title).toBe("Diverged — DIVERGED");
  });

  it("flags an unreachable backend as critical (the LED's twin)", () => {
    expect(
      criticalAlert({
        ...base,
        connectorDown: true,
        connectorName: "termnorm",
        connectorDetail: "All connection attempts failed",
      }),
    ).toEqual({
      severity: "critical",
      title: "Backend unreachable — termnorm",
      detail: "All connection attempts failed",
    });
  });

  it("server-offline takes precedence over a down backend", () => {
    const out = criticalAlert({
      ...base,
      bannerStatus: "offline",
      bannerText: "Server unreachable — retrying",
      connectorDown: true,
      connectorName: "termnorm",
    });
    expect(out?.title).toBe("Server unreachable — retrying");
  });

  it("flags a full machine (no free slot) as a wait, not a refusal", () => {
    expect(
      criticalAlert({
        ...base,
        machineBusy: true,
        machineBusyHolder: "u_bob",
        machineBusySince: "2026-06-08T10:00:00+00:00",
      }),
    ).toEqual({
      severity: "warn",
      title: "Machine full — u_bob is running",
      detail: "a launch will queue · oldest run since 2026-06-08T10:00:00+00:00",
    });
  });

  it("a launch of the caller's own in the queue outranks the full machine", () => {
    expect(
      criticalAlert({
        ...base,
        machineBusy: true,
        machineBusyHolder: "u_bob",
        queuePosition: 2,
      }),
    ).toEqual({
      severity: "warn",
      title: "Queued — position 2",
      detail: "it starts by itself when a slot frees",
    });
  });

  it("a down backend takes precedence over machine-busy", () => {
    const out = criticalAlert({
      ...base,
      connectorDown: true,
      connectorName: "termnorm",
      machineBusy: true,
      machineBusyHolder: "u_bob",
    });
    expect(out?.title).toBe("Backend unreachable — termnorm");
  });

  const roundWithGrade = (round: number, grade: string, suggested: string | null) => ({
    round,
    accuracy: 0.16,
    composite_fitness: 0.5,
    candidates: [],
    selection: [],
    health: {
      grade,
      reasons: [],
      samples: 20,
      structural_count: grade === "critical" ? 12 : 0,
      transient_count: 5,
      degraded_rate: 0.3,
      consecutive_degraded_rounds: 1,
      prior_clean_rounds: 0,
      dominant_node: "entity_profiling",
      ci_lo: 0.03,
      ci_hi: 0.3,
      suggested_action: suggested,
    },
  });

  it("flags a structurally-critical latest round as critical with a pause action", () => {
    const dash = {
      rounds: [roundWithGrade(0, "critical", "entity_profiling failing on 60% — abort")],
    } as unknown as DashboardSnapshot;
    expect(criticalAlert({ ...base, dash })).toEqual({
      severity: "critical",
      title: "Degraded origin — pipeline may be structurally broken",
      detail: "entity_profiling failing on 60% — abort",
      action: "pause",
    });
  });

  it("stays quiet when the latest round is merely degraded (noise — keep going)", () => {
    const dash = {
      rounds: [roundWithGrade(3, "degraded", null)],
    } as unknown as DashboardSnapshot;
    expect(criticalAlert({ ...base, dash })).toBeNull();
  });
});
