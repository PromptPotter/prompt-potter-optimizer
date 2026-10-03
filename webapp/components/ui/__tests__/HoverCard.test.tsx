// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HoverCard } from "../HoverCard";

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

// jsdom measures everything as zero, so placement needs a stubbed trigger rect.
function stubTriggerLowLeft() {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    top: 700,
    left: 0,
    right: 200,
    bottom: 720,
    width: 200,
    height: 20,
    x: 0,
    y: 700,
    toJSON: () => ({}),
  });
}

function settle() {
  act(() => {
    vi.advanceTimersByTime(1000);
  });
}

const wrapOf = (triggerText: string) => screen.getByText(triggerText).parentElement!;

describe("HoverCard", () => {
  it("mounts its content only while hovered", () => {
    render(
      <HoverCard content={<span>on disk</span>}>
        <button type="button">row</button>
      </HoverCard>,
    );
    expect(screen.queryByRole("note")).toBeNull();

    fireEvent.mouseEnter(wrapOf("row"));
    expect(screen.getByRole("note").textContent).toContain("on disk");

    fireEvent.mouseLeave(wrapOf("row"));
    settle();
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("also opens on keyboard focus", () => {
    render(
      <HoverCard content={<span>meta</span>}>
        <button type="button">row</button>
      </HoverCard>,
    );
    fireEvent.focus(wrapOf("row"));
    expect(screen.getByRole("note").textContent).toContain("meta");
  });

  it("stays open when the pointer crosses into the card", () => {
    render(
      <HoverCard content={<span>cycle_62839439e429</span>}>
        <button type="button">row</button>
      </HoverCard>,
    );
    fireEvent.mouseEnter(wrapOf("row"));
    fireEvent.mouseLeave(wrapOf("row"));
    fireEvent.mouseEnter(screen.getByRole("note"));
    settle();
    expect(screen.getByRole("note").textContent).toContain("cycle_62839439e429");

    fireEvent.mouseLeave(screen.getByRole("note"));
    settle();
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("keeps the card open while focus moves into it", () => {
    render(
      <HoverCard
        content={
          <button type="button">copy</button>
        }
      >
        <button type="button">row</button>
      </HoverCard>,
    );
    fireEvent.focus(wrapOf("row"));
    fireEvent.blur(wrapOf("row"), { relatedTarget: screen.getByText("copy") });
    settle();
    expect(screen.getByText("copy")).toBeTruthy();
  });

  it("grows away from the nearer viewport edge, and re-entering does not move it", () => {
    stubTriggerLowLeft();
    render(
      <HoverCard content={<span>meta</span>}>
        <button type="button">row</button>
      </HoverCard>,
    );
    fireEvent.mouseEnter(wrapOf("row"));
    const card = screen.getByRole("note");
    expect(card.style.bottom).toBe("48px");
    expect(card.style.top).toBe("");
    expect(card.style.left).toBe("208px");

    fireEvent.mouseEnter(card);
    expect(card.style.bottom).toBe("48px");
  });

  it("dismisses on Escape", () => {
    render(
      <HoverCard content={<span>meta</span>}>
        <button type="button">row</button>
      </HoverCard>,
    );
    fireEvent.mouseEnter(wrapOf("row"));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("note")).toBeNull();
  });
});
