// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CopyButton } from "../CopyButton";

afterEach(cleanup);

describe("CopyButton", () => {
  const writeText = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    writeText.mockClear();
    Object.assign(navigator, { clipboard: { writeText } });
  });

  it("stringifies object data as pretty JSON", async () => {
    render(<CopyButton data={{ a: 1 }} />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith('{\n  "a": 1\n}'),
    );
  });

  it("copies a string payload verbatim", async () => {
    render(<CopyButton data="hello" />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("hello"));
  });

  it("flashes a copied confirmation in its accessible name", async () => {
    render(<CopyButton data="x" title="Copy node" />);
    const btn = screen.getByRole("button", { name: "Copy node" });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Copied" })).toBeTruthy(),
    );
  });
});
