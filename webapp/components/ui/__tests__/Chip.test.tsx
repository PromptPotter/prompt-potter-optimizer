// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Chip, ChipGroup } from "../Chip";

afterEach(cleanup);

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
