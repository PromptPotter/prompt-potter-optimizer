import { describe, expect, it } from "vitest";
import { connectorReachability } from "../connector-state";
import { type BackendHealthResponse, BACKEND_REACHABILITY_INFO } from "@/lib/api/types";

const health = (status: BackendHealthResponse["status"]): BackendHealthResponse => ({
  status,
  checked_at: "2026-06-03T00:00:00Z",
  detail: null,
});

describe("connectorReachability", () => {
  it("probing when not yet polled — neither reachable nor down", () => {
    expect(connectorReachability(null)).toMatchObject({ reachable: false, stateCls: "idle" });
  });

  it("live when the probe reports live", () => {
    expect(connectorReachability(health("live"))).toEqual({
      reachable: true,
      stateCls: "live",
      stateLabel: BACKEND_REACHABILITY_INFO.live.label,
    });
  });

  it("down for any non-live status, worded by its own served status", () => {
    for (const s of ["unreachable", "missing_extra"] as const) {
      expect(connectorReachability(health(s))).toEqual({
        reachable: false,
        stateCls: "offline",
        stateLabel: BACKEND_REACHABILITY_INFO[s].label,
      });
    }
  });
});
