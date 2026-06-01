// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Popover } from "../Popover";

afterEach(cleanup);

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

  it("closes on Escape", () => {
    render(<Harness />);
    fireEvent.click(screen.getByText(/trigger/));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByText("item")).toBeNull();
  });

  it("closes when content calls close()", () => {
    render(<Harness />);
    fireEvent.click(screen.getByText(/trigger/));
    fireEvent.click(screen.getByText("item"));
    expect(screen.queryByText("item")).toBeNull();
  });
});
