import { describe, expect, it } from "vitest";
import {
  freeText,
  joinSegments,
  segmentPrompt,
  withFreeText,
  type KnownSnippet,
  type Segment,
} from "../prompt-segments";

const S1: KnownSnippet = { text: "Think step by step.", from: "a", edited: false };
const S2: KnownSnippet = { text: "Answer in one word.", from: "b", edited: false };
const KNOWN = [S1, S2];
type Free = Extract<Segment, { kind: "free" }>;

describe("segmentPrompt", () => {
  it("round-trips every text and keeps the free, (snippet, free)* shape", () => {
    for (const text of [
      "",
      "free only",
      S1.text,
      `${S1.text}\n`,
      `\n${S1.text}`,
      `lead\n${S1.text}`,
      `${S1.text}\n${S2.text}`,
      `${S1.text}\n\n${S2.text}`,
      `${S1.text}\nmid\n${S2.text}\ntail`,
      `inline ${S1.text} is no snippet`,
    ]) {
      const segments = segmentPrompt(text, KNOWN);
      expect(joinSegments(segments), text).toBe(text);
      expect(
        segments.map((s) => s.kind),
        text,
      ).toEqual(segments.map((_, i) => (i % 2 === 0 ? "free" : "snippet")));
    }
  });

  it("writes into a box between and after snippets as text on its own lines", () => {
    const segments = segmentPrompt(`${S1.text}\n${S2.text}`, KNOWN);
    const between = segments[2] as Free;
    expect(freeText(between)).toBe("");
    const typed = segments.map((s, i) => (i === 2 ? withFreeText(between, "mid") : s));
    expect(joinSegments(typed)).toBe(`${S1.text}\nmid\n${S2.text}`);
    const last = segments[4] as Free;
    const appended = segments.map((s, i) => (i === 4 ? withFreeText(last, "tail") : s));
    expect(joinSegments(appended)).toBe(`${S1.text}\n${S2.text}\ntail`);
    expect(segmentPrompt(joinSegments(appended), KNOWN).filter((s) => s.kind === "snippet")).toHaveLength(2);
  });
});
