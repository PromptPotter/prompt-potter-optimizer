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

  // A thunk resolves at the click, not at the render — what a payload that stamps its own
  // capture time depends on.
  it("resolves a thunk payload when clicked", async () => {
    const build = vi.fn(() => "late");
    render(<CopyButton data={build} />);
    expect(build).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("late"));
  });

  it("flashes a copied confirmation in its accessible name", async () => {
    render(<CopyButton data="x" title="Copy node" />);
    const btn = screen.getByRole("button", { name: "Copy node" });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Copied" })).toBeTruthy(),
    );
  });

  it("copies the picked reading when several are offered", async () => {
    render(
      <CopyButton
        title="Copy point"
        choices={[
          { key: "spec", label: "Searchpoint spec", data: { spec: 1 } },
          { key: "scored", label: "Spec + scores", data: { spec: 1, scored: 2 } },
        ]}
      />,
    );
    // Closed until asked for: the readings are not on screen before the trigger is clicked.
    expect(screen.queryByRole("menuitem")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Copy point" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Spec + scores" }));
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith('{\n  "spec": 1,\n  "scored": 2\n}'),
    );
  });

  // A one-option group is not a choice — the row's payload rides the plain button, and its
  // label joins the button's name so the one reading is still said out loud.
  it("skips the menu for a single reading", async () => {
    render(<CopyButton title="Copy point" choices={[{ key: "spec", label: "Spec", data: "only" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy point — Spec" }));
    expect(screen.queryByRole("menuitem")).toBeNull();
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("only"));
  });

  // Nothing to hand over renders nothing: a trigger onto an empty menu is a control that
  // looks operable and is not.
  it("renders nothing when no reading is available", () => {
    render(<CopyButton title="Copy point" choices={[]} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});
