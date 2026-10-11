import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Term } from "../Term";

describe("Term", () => {
  it("makes its trigger focusable, and opens on that focus", () => {
    render(<Term content="the metric the winner is elected on">θ</Term>);
    const trigger = screen.getByText("θ");
    expect(trigger.tabIndex).toBe(0);

    fireEvent.focus(trigger);
    expect(screen.getByRole("note").textContent).toContain("elected on");
  });

  it("keeps the hint decoration when a host passes a class", () => {
    render(
      <Term className="chip" content="lift over origin">
        Lift
      </Term>,
    );
    expect(screen.getByText("Lift").className.split(" ").length).toBe(2);
  });
});
