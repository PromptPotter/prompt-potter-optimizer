// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ValueList } from "../ValueList";

afterEach(cleanup);

describe("ValueList", () => {
  const trigger = () => screen.getByRole("button", { name: /^model:/ });
  const open = () => fireEvent.click(trigger());
  const disabled = (el: HTMLElement) => (el as HTMLInputElement | HTMLButtonElement).disabled;

  it("is ONE LINE closed, and the list is what opens — no second copy of the value", () => {
    render(<ValueList name="model" values={["a", "b"]} onPick={() => {}} />);
    expect(trigger().textContent).toContain("a");
    expect(screen.queryByRole("listbox")).toBeNull();
    open();
    expect(screen.getByRole("listbox")).toBeTruthy();
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["a", "b"]);
    expect(screen.getByRole("option", { name: "a" }).getAttribute("aria-selected")).toBe("true");
  });

  it("closes on a pick — that is the whole gesture — and stays open on a tick", () => {
    render(
      <ValueList
        name="model"
        values={["a", "b"]}
        checked={["a", "b"]}
        onPick={() => {}}
        onToggle={() => {}}
      />,
    );
    open();
    fireEvent.click(screen.getByRole("checkbox", { name: "Permit b for model" }));
    expect(screen.getByRole("listbox")).toBeTruthy();
    fireEvent.click(screen.getByRole("option", { name: "b" }));
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("picks by clicking a value — the caller reorders, so row 1 is inert", () => {
    const onPick = vi.fn();
    render(<ValueList name="model" values={["a", "b"]} onPick={onPick} />);
    open();
    // Asserted BEFORE the pick, which closes the panel.
    expect(disabled(screen.getByRole("option", { name: "a" }))).toBe(true);
    fireEvent.click(screen.getByRole("option", { name: "b" }));
    expect(onPick).toHaveBeenCalledWith("b");
  });

  it("ticks are the permitted set, and the LAST one cannot be removed", () => {
    const onToggle = vi.fn();
    const { rerender } = render(
      <ValueList
        name="model"
        values={["a", "b"]}
        checked={["a", "b"]}
        onPick={() => {}}
        onToggle={onToggle}
      />,
    );
    open();
    expect(screen.getByText("2/2")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Permit b for model" }));
    expect(onToggle).toHaveBeenCalledWith("b");

    rerender(
      <ValueList
        name="model"
        values={["a", "b"]}
        checked={["a"]}
        onPick={() => {}}
        onToggle={onToggle}
      />,
    );
    expect(disabled(screen.getByRole("checkbox", { name: "Permit a for model" }))).toBe(true);
    expect(disabled(screen.getByRole("checkbox", { name: "Permit b for model" }))).toBe(false);
  });

  it("an inert value can be UNticked but never ticked — the state must be leavable", () => {
    render(
      <ValueList
        name="model"
        values={["a", "b", "c"]}
        checked={["a", "b"]}
        inert={["b", "c"]}
        onPick={() => {}}
        onToggle={() => {}}
      />,
    );
    open();
    expect(disabled(screen.getByRole("checkbox", { name: "Permit b for model" }))).toBe(false);
    expect(disabled(screen.getByRole("checkbox", { name: "Permit c for model" }))).toBe(true);
    expect(screen.getByRole("option", { name: "c" })).toBeTruthy();
  });

  it("the free-text row commits on Enter and on blur, never per keystroke", () => {
    const onAdd = vi.fn();
    render(
      <ValueList
        name="model"
        values={["a"]}
        checked={["a"]}
        addPlaceholder="another model id…"
        onPick={() => {}}
        onAdd={onAdd}
      />,
    );
    open();
    const input = screen.getByPlaceholderText("another model id…");
    fireEvent.change(input, { target: { value: "org/new-model" } });
    expect(onAdd).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onAdd).toHaveBeenCalledWith("org/new-model");
  });

  it("typing a value already on the menu PICKS it rather than adding a duplicate", () => {
    const onPick = vi.fn();
    const onAdd = vi.fn();
    render(
      <ValueList
        name="model"
        values={["a", "b"]}
        addPlaceholder="another model id…"
        onPick={onPick}
        onAdd={onAdd}
      />,
    );
    open();
    const input = screen.getByPlaceholderText("another model id…");
    fireEvent.change(input, { target: { value: "  b  " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onPick).toHaveBeenCalledWith("b");
    expect(onAdd).not.toHaveBeenCalled();
  });

  it("drops the tick column entirely when the host holds no permitted set", () => {
    render(<ValueList name="model" values={["a", "b"]} onPick={() => {}} />);
    expect(screen.queryByText("0/2")).toBeNull();
    open();
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  });

  it("tags a value the operator typed — nothing in the catalogue vouches for it", () => {
    render(
      <ValueList
        name="model"
        values={["a", "org/typed"]}
        checked={["a", "org/typed"]}
        userAdded={["org/typed"]}
        onPick={() => {}}
        onToggle={() => {}}
      />,
    );
    open();
    expect(screen.getByRole("option", { name: /org\/typed/ }).textContent).toContain("user");
    expect(screen.getByRole("option", { name: "a" }).textContent).not.toContain("user");
  });

  it("drops the START where the host owns no value channel — permissions only", () => {
    render(
      <ValueList name="model" values={["a", "b"]} checked={["a", "b"]} onToggle={() => {}} />,
    );
    expect(trigger().textContent).toContain("a, b");
    open();
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.getByRole("group", { name: "model" })).toBeTruthy();
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
  });

  it("a host with neither channel prints the value the point runs", () => {
    render(<ValueList name="model" values={["a", "b"]} />);
    expect(trigger().textContent).toContain("a");
    expect(trigger().textContent).not.toContain("(none)");
    open();
    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  });

  it("readOnly disables every control but still renders the whole menu", () => {
    render(
      <ValueList
        name="model"
        values={["a", "b"]}
        checked={["a", "b"]}
        readOnly
        addPlaceholder="another model id…"
        onPick={() => {}}
        onToggle={() => {}}
        onAdd={() => {}}
      />,
    );
    open();
    expect(disabled(screen.getByRole("option", { name: "b" }))).toBe(true);
    expect(disabled(screen.getByRole("checkbox", { name: "Permit b for model" }))).toBe(true);
    expect(screen.queryByPlaceholderText("another model id…")).toBeNull();
  });
});
