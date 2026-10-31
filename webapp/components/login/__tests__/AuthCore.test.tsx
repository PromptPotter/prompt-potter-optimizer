import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AuthCore } from "../AuthCore";

describe("AuthCore", () => {
  it("tells a refused sign-up that the server is full, not that something went wrong", () => {
    render(<AuthCore errorCode="accounts_full" />);
    expect(screen.getByRole("alert").textContent).toMatch(/account limit/);
  });
});
