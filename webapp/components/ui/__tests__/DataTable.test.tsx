// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DataTable, type Column } from "../DataTable";

afterEach(cleanup);

interface Row {
  id: string;
  v: number;
}
const COLS: readonly Column<Row>[] = [
  { id: "id", label: "Id", width: "80px", cell: (r) => r.id },
  { id: "v", label: "V", width: "80px", cell: (r) => String(r.v) },
];
const rowId = (r: Row) => r.id;

describe("DataTable", () => {
  it("renders rows in the order given — it has no sort of its own", () => {
    render(
      <DataTable
        ariaLabel="t"
        columns={COLS}
        rows={[
          { id: "b", v: 2 },
          { id: "a", v: 1 },
        ]}
        getRowId={rowId}
      />,
    );
    const ids = screen.getAllByRole("row").slice(1).map((r) => r.textContent);
    expect(ids).toEqual(["b2", "a1"]);
  });

  it("folds a group and unfolds it on click", () => {
    render(
      <DataTable
        ariaLabel="t"
        columns={COLS}
        groups={[{ key: "g", header: "Group g", rows: [{ id: "x", v: 9 }] }]}
        getRowId={rowId}
      />,
    );
    expect(screen.queryByText("x")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Group g/ }));
    expect(screen.getByText("x")).toBeTruthy();
  });

  it("marks the active row and reports a click", () => {
    const onRowClick = vi.fn();
    render(
      <DataTable
        ariaLabel="t"
        columns={COLS}
        rows={[
          { id: "a", v: 1 },
          { id: "b", v: 2 },
        ]}
        getRowId={rowId}
        activeRowId="b"
        onRowClick={onRowClick}
      />,
    );
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((r) => r.getAttribute("aria-current"))).toEqual([null, "true"]);
    fireEvent.click(rows[0]!);
    expect(onRowClick).toHaveBeenCalledWith({ id: "a", v: 1 });
  });
});
