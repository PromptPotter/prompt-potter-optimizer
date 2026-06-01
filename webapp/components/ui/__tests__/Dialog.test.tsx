// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dialog } from "../Dialog";

afterEach(cleanup);

describe("Dialog", () => {
  it("renders nothing when closed", () => {
    render(
      <Dialog open={false} title="T" onClose={() => {}}>
        body
      </Dialog>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("exposes title as the accessible name and renders body + footer", () => {
    render(
      <Dialog open title="My Title" onClose={() => {}} footer={<button>OK</button>}>
        <p>hello body</p>
      </Dialog>,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-label")).toBe("My Title");
    expect(screen.getByText("hello body")).toBeTruthy();
    expect(screen.getByRole("button", { name: "OK" })).toBeTruthy();
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(
      <Dialog open title="T" onClose={onClose}>
        x
      </Dialog>,
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on backdrop press but not on card press", () => {
    const onClose = vi.fn();
    render(
      <Dialog open title="T" onClose={onClose}>
        x
      </Dialog>,
    );
    const card = screen.getByRole("dialog");
    fireEvent.mouseDown(card);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(card.parentElement as HTMLElement);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("moves focus in on open and restores it on close", () => {
    const outside = document.createElement("button");
    outside.textContent = "outside";
    document.body.appendChild(outside);
    outside.focus();
    expect(document.activeElement).toBe(outside);

    const onClose = () => {};
    const { rerender } = render(
      <Dialog open title="T" onClose={onClose}>
        <button>inside</button>
      </Dialog>,
    );
    expect(document.activeElement?.textContent).toBe("inside");

    rerender(
      <Dialog open={false} title="T" onClose={onClose}>
        <button>inside</button>
      </Dialog>,
    );
    expect(document.activeElement).toBe(outside);
    document.body.removeChild(outside);
  });
});
