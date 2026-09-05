// The browser's half of `domain/spend.py::TokenAccount` — one reading of the provider's
// prefix-cache discount, and one fold of the per-node `step_tokens` that carries it. One module
// rather than a helper per renderer: five panes show this share and they have to agree.

import { fmtPct0 } from "@/lib/format";

/** One `step_tokens` entry's counts, or a whole row's folded over its nodes. */
export interface TokenAccount {
  input: number;
  output: number;
  /** `null` where no node reported a breakdown — which is NOT `0`: one says the provider could
   *  not tell us, the other that it did and there was no hit. */
  cacheRead: number | null;
}

/**
 * Fraction of `input` the PROVIDER served off its own prompt-prefix cache.
 *
 * `null` wherever that is unanswerable, `replayed` included: OUR archive served the call, so the
 * counts are the banked row's and a discount beside 📖 claims one this run never got — which is
 * why it is a required argument. `0` is a MEASUREMENT; a caller wanting silence tests `> 0`.
 */
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

/**
 * The ONE rendering of a prefix-cache share, so no surface invents its own threshold and
 * *unreported* ("never asked") cannot render as a cold *0%*.
 *
 * `replayed` is passed rather than inferred: `cacheShare` folds it into `null`, and every call
 * site already holds it beside the share.
 */
export function prefixReading(share: number | null, replayed: boolean): PrefixReading {
  if (replayed) return { state: "replayed", share: null, label: "", title: PREFIX_TITLE.replayed };
  if (share == null)
    return { state: "unreported", share: null, label: "c?", title: PREFIX_TITLE.unreported };
  const state: PrefixState = share > 0 ? "discounted" : "cold";
  return { state, share, label: `c${fmtPct0(share)}`, title: PREFIX_TITLE[state] };
}

/**
 * A round document's per-sample account, folded over `pipeline_data.step_tokens`.
 *
 * The live half is served already folded (`TokenAccount.from_step_tokens`); a historical row comes
 * off the round file, which carries only the per-node entries. Two sources by design
 * (`webapp/CLAUDE.md` § Display-data sources), so this converts the second — it is not a second
 * definition of the fold, and it matches the Python one arm for arm, mixed rows included.
 *
 * `null` where the row has no entries at all, which stays distinct from a reported 0%.
 */
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
