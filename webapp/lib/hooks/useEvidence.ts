import { useEffect, useRef } from "react";
import { evidenceRead } from "@/lib/api/reads";
import type { Evidence } from "@/lib/api/types";
import { useRead } from "@/lib/hooks/useRead";
import { reaskRead, type ReadResult } from "@/lib/read-cache";

export type EvidenceAsk = Parameters<typeof evidenceRead>[1];

// What the measured courses are doing: `live` is the served attachment (`producer.attached` on the
// dashboard, `state.attached` on a course entry); `moved` changes when any of them filed a round,
// scored a cell or changed phase.
export interface EvidencePulse {
  live: boolean;
  moved: string;
}

export function useEvidence(
  subjects: readonly string[],
  pulse: EvidencePulse,
  ask: EvidenceAsk = {},
): ReadResult<Evidence> {
  const asked = subjects.length > 0 ? evidenceRead(subjects, ask, pulse.live) : null;
  const read = useRead(asked);
  const id = asked?.id ?? null;

  // A settled course's move re-asks it; the mount's own first read is not a move.
  const seenRef = useRef(pulse.moved);
  useEffect(() => {
    if (seenRef.current === pulse.moved) return;
    seenRef.current = pulse.moved;
    if (id !== null) reaskRead(id);
  }, [pulse.moved, id]);

  return read;
}
