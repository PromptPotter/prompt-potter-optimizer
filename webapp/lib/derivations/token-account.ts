// The browser's half of `domain/spend.py::TokenAccount`: the one reading of the prefix-cache
// discount and the one fold of `step_tokens`, so every pane showing the share agrees.

import { fmtPct0 } from "@/lib/format";

export interface TokenAccount {
  input: number;
  output: number;
  /** `null` where no node reported a breakdown — which is NOT `0`: one says the provider could
   *  not tell us, the other that it did and there was no hit. */
  cacheRead: number | null;
}

/** `null` when `replayed`: our archive served the call, so a discount would claim one this run
 *  never got. `0` is a MEASUREMENT; a caller wanting silence tests `> 0`. */
export function cacheShare(
  cacheRead: number | null | undefined,
  input: number | null | undefined,
  replayed: boolean,
): number | null {
  if (replayed) return null;
  if (typeof cacheRead !== "number" || typeof input !== "number" || input <= 0) return null;
  return cacheRead / input;
}

export type PrefixState = "discounted" | "cold" | "unreported" | "replayed";

export interface PrefixReading {
  state: PrefixState;
  /** `null` on `unreported` and `replayed`. */
  share: number | null;
  /** What goes on screen. Empty on `replayed` — that row already wears 📖. */
  label: string;
  title: string;
}

const PREFIX_TITLE: Record<PrefixState, string> = {
  discounted:
    "The provider served this share of the input off its own prompt-prefix cache, billed at a discount. Unrelated to 📖, which means no provider was reached at all.",
  cold: "The provider reported its cache accounting and served none of this input from it — the prefix was cold. A measurement, not a missing one.",
  unreported:
    "This provider reported no cache accounting, so whether it collected the prefix is unknown. Not the same as no hit.",
  replayed: "Replayed from our own archive — no provider was reached, so there is no discount to report.",
};

/** The one rendering of a prefix-cache share, so *unreported* never renders as a cold 0%. */
export function prefixReading(share: number | null, replayed: boolean): PrefixReading {
  if (replayed) return { state: "replayed", share: null, label: "", title: PREFIX_TITLE.replayed };
  if (share == null)
    return { state: "unreported", share: null, label: "c?", title: PREFIX_TITLE.unreported };
  const state: PrefixState = share > 0 ? "discounted" : "cold";
  return { state, share, label: `c${fmtPct0(share)}`, title: PREFIX_TITLE[state] };
}

/** Must match `TokenAccount.from_step_tokens` arm for arm — the round file carries only per-node
 *  entries. `null` (no entries at all) stays distinct from a reported 0%. */
export function foldStepTokens(stepTokens: unknown): TokenAccount | null {
  if (typeof stepTokens !== "object" || stepTokens === null) return null;
  const entries = Object.values(stepTokens as Record<string, unknown>).filter(
    (e): e is Record<string, unknown> => typeof e === "object" && e !== null,
  );
  if (entries.length === 0) return null;
  let cacheRead: number | null = null;
  let input = 0;
  let output = 0;
  for (const entry of entries) {
    if (typeof entry["input"] === "number") input += entry["input"];
    if (typeof entry["output"] === "number") output += entry["output"];
    if (typeof entry["cache_read"] === "number") cacheRead = (cacheRead ?? 0) + entry["cache_read"];
  }
  return { input, output, cacheRead };
}
