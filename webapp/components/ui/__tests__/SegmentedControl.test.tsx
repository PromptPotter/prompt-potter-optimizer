// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SegmentedControl, type Segment } from "../SegmentedControl";

afterEach(cleanup);

const VIEWS: readonly Segment<"sequence" | "forest">[] = [
  { value: "sequence", label: "Sequence" },
  { value: "forest", label: "Forest" },
];

describe("SegmentedControl", () => {
  it("presses exactly one segment — the group cannot be empty or multi-selected", () => {
    render(
      <SegmentedControl options={VIEWS} value="sequence" onChange={() => {}} ariaLabel="View" />,
    );
    const pressed = screen
      .getAllByRole("button")
      .filter((b) => b.getAttribute("aria-pressed") === "true");
    expect(pressed).toHaveLength(1);
    expect(pressed[0]?.textContent).toBe("Sequence");
  });

  it("reports the picked value", () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl options={VIEWS} value="sequence" onChange={onChange} ariaLabel="View" />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Forest" }));
    expect(onChange).toHaveBeenCalledWith("forest");
  });

  it("names the group, so the segments don't each restate it", () => {
    render(
      <SegmentedControl
        options={VIEWS}
        value="forest"
        onChange={() => {}}
        ariaLabel="Candidates view"
      />,
    );
    expect(screen.getByRole("group").getAttribute("aria-label")).toBe("Candidates view");
  });

  it("renders an unavailable option, but doesn't let it be picked", () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl
        options={[
          { value: "llm", label: "LLM only" },
          { value: "research", label: "Research + Match", disabled: true },
        ]}
        value="llm"
        onChange={onChange}
        ariaLabel="Pipeline mode"
      />,
    );
    // Still discoverable — the operator sees the mode exists.
    const research = screen.getByRole("button", { name: "Research + Match" });
    fireEvent.click(research);
    expect(onChange).not.toHaveBeenCalled();
  });
});
