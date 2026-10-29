import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ReadResult } from "@/lib/hooks/useRead";
import { FailureLine, ReadState } from "../states";

const failure = { kind: "transient" as const, message: "Could not reach the server." };

function view(read: ReadResult<string[]>) {
  return render(
    <ReadState
      read={read}
      subject="the list"
      idle="Pick one."
      empty={(rows) => (rows.length === 0 ? "Nothing yet." : null)}
    >
      {(rows) => rows.join(",")}
    </ReadState>,
  );
}

describe("ReadState", () => {
  it("renders the idle node before any read is issued", () => {
    view({ status: "idle" });
    expect(screen.getByText("Pick one.")).toBeTruthy();
  });

  it("names the subject while loading", () => {
    view({ status: "loading", kept: null });
    expect(screen.getByRole("status").textContent).toBe("Reading the list…");
  });

  it("renders the failure's sentence behind the subject", () => {
    view({ status: "failed", failure, kept: null });
    expect(screen.getByRole("alert").textContent).toBe(
      "Could not load the list. Could not reach the server.",
    );
  });

  it("keeps rendering kept data through a failure", () => {
    view({ status: "failed", failure, kept: ["a"] });
    expect(screen.getByText("a")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("renders the empty node for empty data, the children otherwise", () => {
    const { rerender } = view({ status: "ready", data: [] });
    expect(screen.getByText("Nothing yet.")).toBeTruthy();
    rerender(
      <ReadState read={{ status: "ready", data: ["a", "b"] }} subject="the list">
        {(rows) => rows.join(",")}
      </ReadState>,
    );
    expect(screen.getByText("a,b")).toBeTruthy();
  });
});

describe("FailureLine", () => {
  it("renders nothing without a failure", () => {
    const { container } = render(<FailureLine failure={null} />);
    expect(container.textContent).toBe("");
  });

  it("leads the message when asked", () => {
    render(<FailureLine failure={failure} lead="fork:" />);
    expect(screen.getByRole("alert").textContent).toBe("fork: Could not reach the server.");
  });
});
