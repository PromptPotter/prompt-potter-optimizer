import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CommitInput } from "../CommitInput";

describe("CommitInput", () => {
  it("says nothing while the operator types", () => {
    const onCommit = vi.fn();
    render(<CommitInput value="" onCommit={onCommit} aria-label="criterion" />);
    fireEvent.change(screen.getByLabelText("criterion"), { target: { value: "accur" } });
    expect(onCommit).not.toHaveBeenCalled();
  });

  it("commits on Enter and on blur, and never twice for one value", () => {
    const onCommit = vi.fn();
    render(<CommitInput value="" onCommit={onCommit} aria-label="criterion" />);
    const input = screen.getByLabelText("criterion");
    fireEvent.change(input, { target: { value: "accuracy" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onCommit).toHaveBeenCalledWith("accuracy");
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledTimes(1);
  });

  it("resends on Enter a value the caller never adopted, but never on blur", () => {
    const onCommit = vi.fn();
    render(<CommitInput value="" onCommit={onCommit} aria-label="key" />);
    const input = screen.getByLabelText("key");
    fireEvent.change(input, { target: { value: "sk-1" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.blur(input);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onCommit).toHaveBeenCalledTimes(2);
  });

  it("lets the caller ask for the browser's autocomplete", () => {
    render(
      <CommitInput value="" onCommit={() => {}} autoComplete="new-password" aria-label="secret" />,
    );
    expect(screen.getByLabelText("secret").getAttribute("autocomplete")).toBe("new-password");
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
    fireEvent.change(box, { target: { value: '{"answer": ' } });
    fireEvent.blur(box);
    expect(onCommit).not.toHaveBeenCalled();
    expect((box as HTMLTextAreaElement).value).toBe('{"answer": ');

    fireEvent.change(box, { target: { value: '{"answer": {}}' } });
    fireEvent.blur(box);
    expect(onCommit).toHaveBeenCalledWith('{"answer": {}}');
  });

  it("leaves Enter alone on a multi-line value", () => {
    const onCommit = vi.fn();
    render(<CommitInput value="" onCommit={onCommit} rows={4} aria-label="layout" />);
    const box = screen.getByLabelText("layout");
    fireEvent.change(box, { target: { value: "one" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onCommit).not.toHaveBeenCalled();
    fireEvent.blur(box);
    expect(onCommit).toHaveBeenCalledWith("one");
  });

  it("takes a value arriving from elsewhere in the SAME render", () => {
    const { rerender } = render(<CommitInput value="a" onCommit={() => {}} aria-label="cell" />);
    fireEvent.change(screen.getByLabelText("cell"), { target: { value: "typed" } });
    rerender(<CommitInput value="b" onCommit={() => {}} aria-label="cell" />);
    expect(screen.getByLabelText("cell")).toHaveProperty("value", "b");
  });
});
