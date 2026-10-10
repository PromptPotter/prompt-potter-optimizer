import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { pressable } from "../pressable";

describe("pressable", () => {
  it("activates on click, Enter and Space only, and swallows Space's page scroll", () => {
    const onActivate = vi.fn();
    render(<div {...pressable(onActivate)}>row</div>);
    const el = screen.getByRole("button", { name: "row" });
    expect(el.tabIndex).toBe(0);

    fireEvent.click(el);
    fireEvent.keyDown(el, { key: "Enter" });
    expect(fireEvent.keyDown(el, { key: " " })).toBe(false);
    fireEvent.keyDown(el, { key: "a" });
    expect(onActivate).toHaveBeenCalledTimes(3);
  });
});
