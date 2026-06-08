"use client";
// Polls whether ANOTHER user is running a campaign on this single-process
// server (it runs campaigns in sequence — one at a time). When busy, the
// CriticalAlertBanner raises the loud "Machine busy" bar, the always-on twin
// of the 409 `machine_busy` a launch attempt returns. Self-contained (no
// provider) — mirrors the connector health poll: gated on `authed` so an anon
// preview never fires the identity-scoped read, and `onAuthError` halts the
// loop when the session dies instead of 401-storming.

import { useCallback, useState } from "react";
import { fetchMachineStatus, type MachineStatus } from "@/lib/api";
import { useAuthGate } from "@/lib/auth-context";
import { usePoll } from "@/lib/hooks/usePoll";

// Cross-user contention is rare-changing; a 5 s probe matches the connector
// reachability cadence and stays cheap (one tiny JSON read off the jobs dir).
const BUSY_INTERVAL_MS = 5000;

const FREE: MachineStatus = { busy: false, holder: null };

export function useMachineStatus(): MachineStatus {
  const { authed, onAuthError } = useAuthGate();
  const [status, setStatus] = useState<MachineStatus>(FREE);

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
