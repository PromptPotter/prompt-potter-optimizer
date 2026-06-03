"use client";
// Local edit buffer for an "edit then Apply" field. Holds the operator's
// in-progress value and re-syncs to the committed `value` prop whenever it
// changes — the render-phase guarded reset (webapp/CLAUDE.md "State reset on
// prop change"), so a server-applied patch flows back into the input without a
// stale frame. `dirty` is true while the buffer diverges from the committed
// value, driving the Apply button's disabled state. Shared by every ingest
// form field (slug, task, max-rounds, optimizer provider/model).

import { useState } from "react";

export function useAppliableField(value: string): {
  local: string;
  setLocal: (v: string) => void;
  dirty: boolean;
} {
  const [prev, setPrev] = useState(value);
  const [local, setLocal] = useState(value);
  if (value !== prev) {
    setPrev(value);
    setLocal(value);
  }
  return { local, setLocal, dirty: local !== value };
}
