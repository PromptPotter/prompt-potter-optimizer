// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { HoverCard } from "../HoverCard";

afterEach(cleanup);

describe("HoverCard", () => {
  it("mounts its content only while hovered, as a tooltip", () => {
    render(
      <HoverCard content={<span>on disk</span>}>
        <button type="button">row</button>
      </HoverCard>,
    );
    // Closed: content absent (so lazy content never fetches until pointed at).
    expect(screen.queryByRole("tooltip")).toBeNull();

    fireEvent.mouseEnter(screen.getByText("row").parentElement!);
    expect(screen.getByRole("tooltip").textContent).toContain("on disk");

    fireEvent.mouseLeave(screen.getByText("row").parentElement!);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("also opens on keyboard focus", () => {
    render(
      <HoverCard content={<span>meta</span>}>
        <button type="button">row</button>
      </HoverCard>,
    );
    fireEvent.focus(screen.getByText("row").parentElement!);
    expect(screen.getByRole("tooltip").textContent).toContain("meta");
  });
});
