import { act, render, renderHook, screen } from "@testing-library/react";
import type { ActiveElement, ChartEvent } from "chart.js";
import { describe, expect, it } from "vitest";
import { ChartReading, useChartReading } from "../ChartReading";

const mouse = (type: ChartEvent["type"]) =>
  ({ type, native: new MouseEvent(type), x: 0, y: 0 }) as ChartEvent;
// Chart.js files a finger's drag under "mousemove" with the TouchEvent as its native.
const fingerDrag = { type: "mousemove", native: new Event("touchmove"), x: 0, y: 0 } as ChartEvent;
const column = (index: number) => [{ index, datasetIndex: 0 }] as unknown as ActiveElement[];

describe("useChartReading", () => {
  it("a pointer's hover reads a column and leaving clears it", () => {
    const { result } = renderHook(() => useChartReading());
    act(() => result.current.events.onHover(mouse("mousemove"), column(2)));
    expect(result.current.at).toBe(2);
    act(() => result.current.events.onHover(mouse("mouseout"), []));
    expect(result.current.at).toBeNull();
  });

  it("a tap sets the line and a second tap on it clears it; a drag never sets it", () => {
    const { result } = renderHook(() => useChartReading());
    act(() => result.current.events.onHover(fingerDrag, column(1)));
    expect(result.current.at).toBeNull();
    // A tap: the browser's compat mousemove, then the click.
    const tap = (i: number) => {
      act(() => result.current.events.onHover(mouse("mousemove"), column(i)));
      act(() => result.current.events.onClick(mouse("click"), column(i)));
    };
    tap(1);
    expect(result.current.at).toBe(1);
    tap(3);
    expect(result.current.at).toBe(3);
    tap(3);
    expect(result.current.at).toBeNull();
  });

  it("a hover reads over a pin, and leaving falls back to the pin", () => {
    const { result } = renderHook(() => useChartReading());
    act(() => result.current.events.onClick(mouse("click"), column(0)));
    act(() => result.current.events.onHover(mouse("mousemove"), column(4)));
    expect(result.current.at).toBe(4);
    act(() => result.current.events.onHover(mouse("mouseout"), []));
    expect(result.current.at).toBe(0);
  });
});

describe("ChartReading", () => {
  it("is DOM text under the chart: the reading, else the hint", () => {
    const { rerender } = render(<ChartReading hint="Tap a round.">{null}</ChartReading>);
    expect(screen.getByRole("status").textContent).toBe("Tap a round.");
    rerender(<ChartReading hint="Tap a round.">round 2 · l1 $0.0100</ChartReading>);
    expect(screen.getByRole("status").textContent).toBe("round 2 · l1 $0.0100");
  });
});
