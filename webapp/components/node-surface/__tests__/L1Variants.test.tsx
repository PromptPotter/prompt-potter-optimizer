import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { roundOfArms } from "@/lib/test-fixtures";
import { L1Variants } from "../L1Variants";

describe("L1Variants", () => {
  it("names the panel its quote was held by beside the quote", () => {
    const quote = "ESCALATION: L1 stalled 2 rounds";
    const { candidates } = roundOfArms(1, [
      {
        key: "a",
        label: "C1.1",
        evidence_grounding: { field: "critique", citation: "other" },
      },
      {
        key: "b",
        label: "C1.2",
        evidence_grounding: { field: "escalation_panel", citation: quote },
      },
    ]);
    render(
      <L1Variants
        variants={[{ evidence_grounding: { citation: ` ${quote} ` } }]}
        arms={candidates}
      />,
    );
    expect(screen.getByText("escalation_panel")).toBeTruthy();
    expect(screen.queryByText("critique")).toBeNull();
  });
});
