// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CommitInput } from "../CommitInput";

afterEach(cleanup);

describe("CommitInput", () => {
  it("says nothing while the operator types", () => {
    const onCommit = vi.fn();
    render(<CommitInput value="" onCommit={onCommit} aria-label="criterion" />);
    fireEvent.change(screen.getByLabelText("criterion"), { target: { value: "accur" } });
    // Every half-typed value is a valid but WRONG request — one fetch per character, and a 400
    // on each half-written formula.
    expect(onCommit).not.toHaveBeenCalled();
  });

  it("commits on Enter and on blur, and never twice for one value", () => {
    const onCommit = vi.fn();
    render(<CommitInput value="" onCommit={onCommit} aria-label="criterion" />);
    const input = screen.getByLabelText("criterion");
    fireEvent.change(input, { target: { value: "accuracy" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onCommit).toHaveBeenCalledWith("accuracy");
    // The blur that follows an Enter must not re-fire: the committed value is now the prop's,
    // and a second identical request is a second refetch of the same read.
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledTimes(1);
  });

  it("keeps a draft `validate` refuses, and commits it once it parses", () => {
    const onCommit = vi.fn();
    const ok = (d: string) => {
      try {
        JSON.parse(d);
        return true;
      } catch {
        return false;
      }
    };
    render(
      <CommitInput value="{}" onCommit={onCommit} validate={ok} rows={4} aria-label="schema" />,
    );
    const box = screen.getByLabelText("schema");
    // Half-typed JSON must not reach the caller as a STRING — the emitter would drop it, and a
    // widget that accepted what the emitter drops loses the operator's schema in silence.
    fireEvent.change(box, { target: { value: '{"answer": ' } });
    fireEvent.blur(box);
    expect(onCommit).not.toHaveBeenCalled();
    expect((box as HTMLTextAreaElement).value).toBe('{"answer": ');

    fireEvent.change(box, { target: { value: '{"answer": {}}' } });
    fireEvent.blur(box);
    expect(onCommit).toHaveBeenCalledWith('{"answer": {}}');
  });

  it("takes a value arriving from elsewhere in the SAME render", () => {
    const { rerender } = render(<CommitInput value="a" onCommit={() => {}} aria-label="cell" />);
    fireEvent.change(screen.getByLabelText("cell"), { target: { value: "typed" } });
    // A restore, or a channel re-pointed under the cursor. An effect-based reset would paint one
    // frame of "typed" first, which reads as the edit having survived the restore.
    rerender(<CommitInput value="b" onCommit={() => {}} aria-label="cell" />);
    expect((screen.getByLabelText("cell") as HTMLInputElement).value).toBe("b");
  });
});
