import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Chip, ChipGroup } from "../Chip";

describe("Chip", () => {
  it("carries its state in aria-pressed — never colour alone", () => {
    render(
      <>
        <Chip on onClick={() => {}}>Acc</Chip>
        <Chip on={false} onClick={() => {}}>Comp</Chip>
      </>,
    );
    expect(screen.getByRole("button", { name: "Acc" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "Comp" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("a menu's trigger says expanded, never pressed, and an action says neither", () => {
    render(
      <>
        <Chip on expanded={false} onClick={() => {}}>Menu</Chip>
        <Chip onClick={() => {}}>Act</Chip>
      </>,
    );
    const menu = screen.getByRole("button", { name: "Menu" });
    expect(menu.getAttribute("aria-expanded")).toBe("false");
    expect(menu.getAttribute("aria-haspopup")).toBe("menu");
    expect(menu.hasAttribute("aria-pressed")).toBe(false);
    const act = screen.getByRole("button", { name: "Act" });
    expect(act.hasAttribute("aria-pressed")).toBe(false);
    expect(act.hasAttribute("aria-expanded")).toBe(false);
  });

  it("fires on click, and is inert when disabled", () => {
    const onClick = vi.fn();
    const { rerender } = render(
      <Chip on={false} onClick={onClick}>
        Sample set
      </Chip>,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);

    rerender(
      <Chip on={false} onClick={onClick} disabled>
        Sample set
      </Chip>,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("groups chips under one accessible name", () => {
    render(
      <ChipGroup label="Metric">
        <Chip on onClick={() => {}}>Acc</Chip>
      </ChipGroup>,
    );
    expect(screen.getByRole("group").getAttribute("aria-label")).toBe("Metric");
  });
});
