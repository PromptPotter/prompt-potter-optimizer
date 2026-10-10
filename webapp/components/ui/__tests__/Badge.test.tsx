import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Badge } from "../Badge";

describe("Badge", () => {
  it("renders its children", () => {
    render(<Badge>waiting</Badge>);
    expect(screen.getByText("waiting")).toBeTruthy();
  });

  it("forwards the title attribute", () => {
    render(<Badge title="tip">x</Badge>);
    expect(screen.getByText("x").getAttribute("title")).toBe("tip");
  });
});
