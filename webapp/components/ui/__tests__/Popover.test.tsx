import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Popover } from "../Popover";

function Harness() {
  return (
    <Popover
      renderTrigger={({ open, toggle }) => (
        <button onClick={toggle}>trigger {open ? "open" : "closed"}</button>
      )}
    >
      {({ close }) => <button onClick={close}>item</button>}
    </Popover>
  );
}

describe("Popover", () => {
  it("is closed until the trigger toggles it", () => {
    render(<Harness />);
    expect(screen.queryByText("item")).toBeNull();
    fireEvent.click(screen.getByText(/trigger/));
    expect(screen.getByText("item")).toBeTruthy();
  });

  it("closes on outside mousedown", () => {
    render(<Harness />);
    fireEvent.click(screen.getByText(/trigger/));
    fireEvent.mouseDown(document.body);
    expect(screen.queryByText("item")).toBeNull();
  });

  // Portaled out of the wrapper, so "outside" must still exclude the panel itself.
  it("renders the panel outside any clipping ancestor, and a press inside keeps it open", () => {
    const { container } = render(<Harness />);
    fireEvent.click(screen.getByText(/trigger/));
    const item = screen.getByText("item");
    expect(container.contains(item)).toBe(false);
    fireEvent.mouseDown(item);
    expect(screen.getByText("item")).toBe(item);
  });

  it("closes on Escape", () => {
    render(<Harness />);
    fireEvent.click(screen.getByText(/trigger/));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByText("item")).toBeNull();
  });

  it("opens above a low trigger, its edge clamped inside the viewport", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
      new DOMRect(-20, window.innerHeight - 40, 50, 20),
    );
    render(<Harness />);
    fireEvent.click(screen.getByText(/trigger/));
    const panel = screen.getByText("item").parentElement!;
    expect(panel.style.left).toBe("8px");
    expect(panel.style.bottom).toBe("42px");
    expect(panel.style.top).toBe("");
  });

  it("closes when content calls close()", () => {
    render(<Harness />);
    fireEvent.click(screen.getByText(/trigger/));
    fireEvent.click(screen.getByText("item"));
    expect(screen.queryByText("item")).toBeNull();
  });
});
