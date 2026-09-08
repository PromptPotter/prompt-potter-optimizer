// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ValueList } from "../ValueList";

afterEach(cleanup);

// The two facts an axis has, and the two ways this widget states them WITHOUT a marker of its
// own: the start value is position 1, and the permitted set is the ticks.
describe("ValueList", () => {
  // Closed, the axis is one line; the trigger is that line. Its aria-label carries the value, so
  // it is findable whatever the value happens to be.
  const trigger = () => screen.getByRole("button", { name: /^model:/ });
  const open = () => fireEvent.click(trigger());
  const disabled = (el: HTMLElement) => (el as HTMLInputElement | HTMLButtonElement).disabled;

  it("is ONE LINE closed, and the list is what opens — no second copy of the value", () => {
    render(<ValueList name="model" values={["a", "b"]} onPick={() => {}} />);
    // Closed: the trigger is the whole widget and no list exists.
    expect(trigger().textContent).toContain("a");
    expect(screen.queryByRole("listbox")).toBeNull();
    open();
    // Open: the panel covers the trigger (Popover `side="over"`), so the value renders once —
    // as the list's first entry, landing where the closed line sat.
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
    // Narrowing a permitted set is several clicks; a panel that shut on each would make the
    // operator re-open it per value.
    expect(screen.getByRole("listbox")).toBeTruthy();
    fireEvent.click(screen.getByRole("option", { name: "b" }));
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("picks by clicking a value — the caller reorders, so row 1 is inert", () => {
    const onPick = vi.fn();
    render(<ValueList name="model" values={["a", "b"]} onPick={onPick} />);
    open();
    // Row 1 is where the axis already starts; clicking it would be a no-op edit. Asserted
    // BEFORE the pick, which closes the panel.
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

    // One permitted value IS the pin — unticking it would leave nothing to run.
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
    // permitted + inert → the repair is available.
    expect(disabled(screen.getByRole("checkbox", { name: "Permit b for model" }))).toBe(false);
    // not permitted + inert → cannot be turned on.
    expect(disabled(screen.getByRole("checkbox", { name: "Permit c for model" }))).toBe(true);
    // Inert is never HIDDEN: it comes back when the refusal lifts, and a vanished row would
    // read as a schema change.
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
    expect(onAdd).not.toHaveBeenCalled(); // typing is not a request
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
    expect(screen.queryByText("0/2")).toBeNull(); // no count either — there is nothing to count
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

  // The other half of the same law: `checked` absent drops the ticks, `onPick` absent drops the
  // start. A host owning one of the two facts gets no control for the one it cannot honour.
  it("drops the START where the host owns no value channel — permissions only", () => {
    render(
      <ValueList name="model" values={["a", "b"]} checked={["a", "b"]} onToggle={() => {}} />,
    );
    // Closed, the line states what it PERMITS — printing values[0] would read as a start value
    // on a surface that cannot set one.
    expect(trigger().textContent).toContain("a, b");
    open();
    // Not a listbox, and nothing selectable: the rows are labels for their checkboxes.
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.getByRole("group", { name: "model" })).toBeTruthy();
    // The ticks are still the whole control.
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
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
