import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HoverCard } from "../HoverCard";
import { Menu, MenuItem } from "../Menu";

beforeEach(() => {
  vi.useFakeTimers();
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
const hover = (el: Element) => fireEvent.pointerEnter(el, { pointerType: "mouse" });
const unhover = (el: Element) => fireEvent.pointerLeave(el, { pointerType: "mouse" });
const focus = (el: HTMLElement) => fireEvent.focus(el);

describe("HoverCard", () => {
  it("mounts its content only while hovered", () => {
    render(
      <HoverCard content={<span>on disk</span>}>
        <button type="button">row</button>
      </HoverCard>,
    );
    expect(screen.queryByRole("note")).toBeNull();

    hover(wrapOf("row"));
    expect(screen.getByRole("note").textContent).toContain("on disk");

    unhover(wrapOf("row"));
    settle();
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("also opens on keyboard focus", () => {
    render(
      <HoverCard content={<span>meta</span>}>
        <button type="button">row</button>
      </HoverCard>,
    );
    focus(screen.getByText("row"));
    expect(screen.getByRole("note").textContent).toContain("meta");
  });

  it("leaves a tap on a live control inside the trigger to that control", () => {
    render(
      <HoverCard content={<span>meta</span>}>
        <button type="button">row</button>
      </HoverCard>,
    );
    const row = screen.getByText("row");
    fireEvent.pointerEnter(row, { pointerType: "touch" });
    fireEvent.pointerDown(row, { pointerType: "touch" });
    fireEvent.focus(row);
    fireEvent.pointerUp(row, { pointerType: "touch" });
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("leaves a tap in a menu the trigger opened to that menu", () => {
    render(
      <HoverCard content={<span>meta</span>}>
        <Menu renderTrigger={({ toggle }) => <button type="button" onClick={toggle}>⋯</button>}>
          {() => <MenuItem onClick={() => undefined}>Rename</MenuItem>}
        </Menu>
      </HoverCard>,
    );
    fireEvent.click(screen.getByText("⋯"));
    fireEvent.pointerUp(screen.getByText("Rename"), { pointerType: "touch" });
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("opens on a tap through a disabled control, its reason in reach", () => {
    render(
      <HoverCard content={<span>why not</span>}>
        <button type="button" disabled>
          row
        </button>
      </HoverCard>,
    );
    fireEvent.pointerUp(screen.getByText("row"), { pointerType: "touch" });
    expect(screen.getByRole("note").textContent).toContain("why not");
  });

  it("stays open when the pointer crosses into the card", () => {
    render(
      <HoverCard content={<span>cycle_62839439e429</span>}>
        <button type="button">row</button>
      </HoverCard>,
    );
    hover(wrapOf("row"));
    unhover(wrapOf("row"));
    hover(screen.getByRole("note"));
    settle();
    expect(screen.getByRole("note").textContent).toContain("cycle_62839439e429");

    unhover(screen.getByRole("note"));
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
    focus(screen.getByText("row"));
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
    hover(wrapOf("row"));
    const card = screen.getByRole("note");
    expect(card.style.bottom).toBe("48px");
    expect(card.style.top).toBe("");
    expect(card.style.left).toBe("208px");

    hover(card);
    expect(card.style.bottom).toBe("48px");
  });

  it("dismisses on Escape", () => {
    render(
      <HoverCard content={<span>meta</span>}>
        <button type="button">row</button>
      </HoverCard>,
    );
    hover(wrapOf("row"));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("note")).toBeNull();
  });
});
