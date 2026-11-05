import { describe, expect, it } from "vitest";
import { draftCadence } from "@/lib/api/reads";
import { DRAFT_POLL_MS } from "@/lib/cadence";

// The served stage fields of a `DraftReopenResponse`, and whether a write is in flight on the draft.
const state = (over: { start?: "queued" | "starting" | null; task?: string | null } | null, writing = false) => ({
  body:
    over === null
      ? null
      : {
          start: over.start ?? null,
          draft: { raw_task_description: over.task === undefined ? "classify each row" : over.task },
        },
  writing,
});

describe("a draft's cadence is the stage the flow shows", () => {
  it("polls a draft in review while Start's refusal may move, and leaves a lost reopen to the cache", () => {
    expect(draftCadence(state({}))).toBe(DRAFT_POLL_MS);
    expect(draftCadence(state(null))).toBeNull();
  });

  it("rests while it awaits its task or a held Start", () => {
    expect(draftCadence(state({ task: null }))).toBeNull();
    expect(draftCadence(state({ start: "queued" }))).toBeNull();
    expect(draftCadence(state({ start: "starting" }))).toBeNull();
  });

  it("rests while a write is in flight on it: the write's answer re-asks it", () => {
    expect(draftCadence(state({}, true))).toBeNull();
    expect(draftCadence(state(null, true))).toBeNull();
  });
});
