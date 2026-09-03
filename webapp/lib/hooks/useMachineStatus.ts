"use client";
// Polls how full this machine is: how many campaigns it admits right now, how
// many are live, how deep the queue is, and where the caller's own waiting
// launches stand in it. A full box does not refuse — the CriticalAlertBanner
// says "you will wait", or names the position once they are in line. Counts the
// caller's own run too: occupancy is not relative to who asks. Self-contained (no
// provider) — mirrors the connector health poll: gated on `authed` so an anon
// preview never fires the identity-scoped read, and `onAuthError` halts the
// loop when the session dies instead of 401-storming.

import { useCallback, useState } from "react";
import { fetchMachineStatus, type MachineStatusResponse } from "@/lib/api";
import { useAuthGate } from "@/lib/auth-context";
import { usePoll } from "@/lib/hooks/usePoll";

// Contention is rare-changing; a 5 s probe matches the connector
// reachability cadence and stays cheap (one tiny JSON read off the jobs dir).
const BUSY_INTERVAL_MS = 5000;

// Pre-poll placeholder, and what a failed tick falls back to. `busy: false` is the safe
// default — never gate the UI on a poll that has not landed — and the counts read 0 until
// the first tick, so nothing should render them without checking `busy`.
const FREE: MachineStatusResponse = {
  capacity: 0,
  running: 0,
  queued: 0,
  busy: false,
  holder: null,
  queue: [],
};

export function useMachineStatus(): MachineStatusResponse {
  const { authed, onAuthError } = useAuthGate();
  const [status, setStatus] = useState<MachineStatusResponse>(FREE);

  const tick = useCallback(
    async (signal: AbortSignal) => {
      try {
        const s = await fetchMachineStatus(signal);
        if (!signal.aborted) setStatus(s);
      } catch (e) {
        if (!signal.aborted) {
          onAuthError(e);
          setStatus(FREE);
        }
      }
    },
    [onAuthError],
  );

  usePoll(tick, { intervalMs: BUSY_INTERVAL_MS, enabled: authed });
  return status;
}
