// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SidePanel } from "../SidePanel";

afterEach(cleanup);

describe("SidePanel", () => {
  it("steps with j/k only where a row exists that way, and closes on Escape", () => {
    const onStep = vi.fn();
    const onClose = vi.fn();
    render(
      <SidePanel panelId="t" title="T" onClose={onClose} hasPrev={false} hasNext onStep={onStep}>
        body
      </SidePanel>,
    );
    fireEvent.keyDown(window, { key: "j" });
    fireEvent.keyDown(window, { key: "k" });
    expect(onStep.mock.calls).toEqual([[1]]);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("ignores the keys while the operator is typing", () => {
    const onStep = vi.fn();
    const { getByRole } = render(
      <SidePanel panelId="t" title="T" onClose={() => {}} hasPrev hasNext onStep={onStep}>
        <input aria-label="field" />
      </SidePanel>,
    );
    fireEvent.keyDown(getByRole("textbox"), { key: "j" });
    expect(onStep).not.toHaveBeenCalled();
  });
});
