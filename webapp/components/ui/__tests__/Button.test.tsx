// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Button } from "../Button";

afterEach(cleanup);

describe("Button", () => {
  it("renders children and defaults type to button (never submits)", () => {
    render(<Button>Go</Button>);
    expect(screen.getByRole("button", { name: "Go" }).getAttribute("type")).toBe("button");
  });

  it("fires onClick, and is inert when disabled", () => {
    const onClick = vi.fn();
    const { rerender } = render(<Button onClick={onClick}>Hit</Button>);
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);

    rerender(
      <Button onClick={onClick} disabled>
        Hit
      </Button>,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("forwards native attributes (aria, type)", () => {
    render(
      <Button type="submit" aria-label="save">
        S
      </Button>,
    );
    const btn = screen.getByRole("button");
    expect(btn.getAttribute("type")).toBe("submit");
    expect(btn.getAttribute("aria-label")).toBe("save");
  });
});
