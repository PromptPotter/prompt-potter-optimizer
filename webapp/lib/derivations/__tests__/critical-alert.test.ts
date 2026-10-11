import { describe, expect, it } from "vitest";
import { criticalAlert } from "../critical-alert";
import type { DegradationHealth, ProducerReading } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";
import { dash as dashboard, health, producerReading, summaryRound } from "@/lib/test-fixtures";

type ProducerState = ProducerReading["state"];

const base = {
  bannerStatus: "connected" as const,
  bannerText: "",
  bannerHint: undefined,
  dash: null as DashboardSnapshot | null,
};

describe("criticalAlert", () => {
  it("returns null on a clean live run", () => {
    expect(criticalAlert(base)).toBeNull();
  });

  it("flags a crashed run as critical, regardless of connection", () => {
    const dash = dashboard({
      run_phase: "terminal",
      error: {
        kind: "CRASHED",
        message: "boom\nretry",
        stop_reason: "crashed",
        label: "Crashed",
        next_step: "Read the traceback, then resume.",
      },
    });
    expect(criticalAlert({ ...base, dash })).toEqual({
      severity: "critical",
      title: "Crashed — CRASHED",
      detail: "Read the traceback, then resume.",
    });
  });

  it("names a designed halt by its own stop reason rather than calling it a crash", () => {
    const dash = dashboard({
      run_phase: "terminal",
      error: {
        kind: "ResumeDivergenceError",
        message: "optimizer_identity:l1_generate",
        stop_reason: "diverged",
        label: "Diverged",
        next_step: "Fork on the divergence.",
      },
    });
    expect(criticalAlert({ ...base, dash })).toEqual({
      severity: "critical",
      title: "Diverged — ResumeDivergenceError",
      detail: "Fork on the divergence.",
    });
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
    // A first-run account never leaves INITIAL_STATE: without its own signal it paints as an outage.
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
    // The caller subtracts netDown first; this pins that a slip there cannot silence a real outage.
    const out = criticalAlert({
      ...base,
      bannerStatus: "offline",
      bannerText: "Server unreachable — retrying",
      emptyWorkspace: false,
    });
    expect(out?.title).toBe("Server unreachable — retrying");
  });

  const PRODUCER_ALERT: Record<ProducerState, string | null> = {
    live: null,
    idle: null,
    held: null,
    absent: null,
    claimed: null,
    silent: "Run went silent",
    wedged: "Run wedged",
  };

  it.each(Object.entries(PRODUCER_ALERT) as [ProducerState, string | null][])(
    "reads a %s producer off the served state alone",
    (state, title) => {
      const dash = dashboard({ producer: producerReading(state, { silent_for_s: 600 }) });
      expect(criticalAlert({ ...base, dash })?.title ?? null).toBe(title);
    },
  );

  it("raises the served producer alert verbatim", () => {
    const alert = { title: "Run wedged", detail: "heartbeats only for 10m" };
    const dash = dashboard({ producer: producerReading("wedged", { alert }) });
    expect(criticalAlert({ ...base, dash })).toEqual({ severity: "warn", ...alert });
  });

  it("does not flag a clean terminal (no error record)", () => {
    const dash = dashboard({ run_phase: "terminal", stop_reason: "target_hit" });
    expect(criticalAlert({ ...base, dash })).toBeNull();
  });

  it("stays silent for warming_up (reachable, no snapshot yet)", () => {
    expect(criticalAlert({ ...base, dash: null })).toBeNull();
  });

  it("a terminal error takes precedence over offline", () => {
    const dash = dashboard({
      run_phase: "terminal",
      error: {
        kind: "DIVERGED",
        message: "x",
        stop_reason: "diverged",
        label: "Diverged",
        next_step: "",
      },
    });
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

  const machineNotice = { title: "Machine full — u_bob is running", detail: "a launch will queue" };

  it("raises the served machine notice verbatim, as a wait and not a refusal", () => {
    expect(criticalAlert({ ...base, machineNotice })).toEqual({ severity: "warn", ...machineNotice });
  });

  it("a down backend takes precedence over the machine notice", () => {
    const out = criticalAlert({
      ...base,
      connectorDown: true,
      connectorName: "termnorm",
      machineNotice,
    });
    expect(out?.title).toBe("Backend unreachable — termnorm");
  });

  const roundWithGrade = (
    round: number,
    grade: DegradationHealth["grade"],
    suggested: string | null,
    alert: string | null = null,
  ) =>
    summaryRound({
      round,
      health: health(grade, { suggested_action: suggested }),
      health_alert: alert,
    });

  it("raises the latest round's served health alert as critical with a pause action", () => {
    const dash = dashboard({
      rounds: [
        roundWithGrade(
          0,
          "critical",
          "entity_profiling failing on 60% — abort",
          "Degraded origin — pipeline may be structurally broken",
        ),
      ],
    });
    expect(criticalAlert({ ...base, dash })).toEqual({
      severity: "critical",
      title: "Degraded origin — pipeline may be structurally broken",
      detail: "entity_profiling failing on 60% — abort",
      action: "pause",
    });
  });

  it("stays quiet when the latest round is merely degraded (noise — keep going)", () => {
    const dash = dashboard({ rounds: [roundWithGrade(3, "degraded", null)] });
    expect(criticalAlert({ ...base, dash })).toBeNull();
  });
});
