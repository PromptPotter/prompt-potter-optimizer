// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Term } from "../Term";

afterEach(cleanup);

describe("Term", () => {
  // If the trigger stops being focusable, the card is unreachable by keyboard and nothing says so.
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
