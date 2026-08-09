import { describe, expect, it } from "vitest";
import { connectorReachability } from "../connector-state";
import type { BackendHealthResponse } from "@/lib/api";

const health = (status: BackendHealthResponse["status"]): BackendHealthResponse => ({
  backend_id: "b1",
  base_url: "http://127.0.0.1:8000",
  status,
  checked_at: "2026-06-03T00:00:00Z",
  detail: null,
});

describe("connectorReachability", () => {
  it("probing when not yet polled", () => {
    expect(connectorReachability(null)).toMatchObject({
      reachable: false,
      down: false,
      stateCls: "idle",
      stateLabel: "probing…",
    });
  });

  it("live when the probe reports live", () => {
    expect(connectorReachability(health("live"))).toMatchObject({
      reachable: true,
      down: false,
      stateCls: "live",
      stateLabel: "reachable",
    });
  });

  it("down for any non-live status", () => {
    for (const s of ["unreachable", "error"] as const) {
      expect(connectorReachability(health(s))).toMatchObject({
        reachable: false,
        down: true,
        stateCls: "offline",
        stateLabel: "unreachable",
      });
    }
  });
});
