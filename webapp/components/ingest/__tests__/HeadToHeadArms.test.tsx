import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { KnobRow, OptimizerSetup, StartCheckinOptions } from "@/lib/api";
import { HeadToHeadArms, startOptions, type HeadToHeadDraft } from "../HeadToHeadArms";

function knob(key: string, value: number): KnobRow {
  return {
    key,
    description: `${key} of the arm`,
    summary: `${key} of the arm`,
    type: "integer",
    options: null,
    nullable: false,
    minimum: null,
    exclusive_minimum: null,
    maximum: null,
    exclusive_maximum: null,
    declared: value,
    value,
    movable_by: [],
  };
}

const SETUP: OptimizerSetup = {
  main: [{ node: "blocks", knob: "max_blocks" }],
  knobs: [{ node: "blocks", kind: "proposer", knobs: [knob("max_blocks", 8)] }],
  prompts: {},
  arms_per_round: 4,
  proposers: [],
  pools: [],
};

function Harness({ onStart }: { onStart: (sent: StartCheckinOptions) => void }) {
  const [h2h, setH2h] = useState<HeadToHeadDraft>({ id: "duel", arms: [] });
  return (
    <>
      <HeadToHeadArms
        value={h2h}
        onChange={setH2h}
        optimizers={["capo", "potter"]}
        base="capo"
        setup={SETUP}
      />
      <button type="button" onClick={() => onStart(startOptions({ budget: { usd: 2 } }, h2h))}>
        Start
      </button>
    </>
  );
}

function commit(input: HTMLElement, text: string) {
  fireEvent.change(input, { target: { value: text } });
  fireEvent.keyDown(input, { key: "Enter" });
}

describe("HeadToHeadArms", () => {
  it("sends one optimizer twice, apart by a knob, each arm on the launch's budget", () => {
    const onStart = vi.fn();
    render(<Harness onStart={onStart} />);
    const add = screen.getByRole("button", { name: "Add arm" });
    fireEvent.click(add);
    fireEvent.click(add);
    commit(within(screen.getByRole("group", { name: "Arm capo-2" })).getByLabelText("Arm key"), "capo-b5");
    const b5 = screen.getByRole("group", { name: "Arm capo-b5" });
    commit(within(b5).getByLabelText("max_blocks"), "5");
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(onStart).toHaveBeenLastCalledWith({
      head_to_head: {
        head_to_head_id: "duel",
        arms: [
          { arm_key: "capo", optimization: { optimizer: "capo" } },
          {
            arm_key: "capo-b5",
            optimization: { optimizer: "capo", nodes: { blocks: { config: { max_blocks: 5 } } } },
          },
        ],
        budget: { usd: 2 },
      },
    });

    // A knob set back to the setup's value leaves the arm: it then runs what the sibling runs.
    commit(within(b5).getByLabelText("max_blocks"), "8");
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    const sent = onStart.mock.lastCall?.[0] as StartCheckinOptions;
    expect(sent.head_to_head?.arms?.[1]).toEqual({
      arm_key: "capo-b5",
      optimization: { optimizer: "capo" },
    });
  });

  it("starts one campaign, its launch untouched, while no arm is declared", () => {
    const onStart = vi.fn();
    render(<Harness onStart={onStart} />);
    fireEvent.click(screen.getByRole("button", { name: "Add arm" }));
    fireEvent.click(within(screen.getByRole("group", { name: "Arm capo" })).getByRole("button", { name: "Remove" }));
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(onStart).toHaveBeenLastCalledWith({ budget: { usd: 2 } });
  });

  it("drops an arm's knobs when it switches optimizer: they were the other one's", () => {
    const onStart = vi.fn();
    render(<Harness onStart={onStart} />);
    fireEvent.click(screen.getByRole("button", { name: "Add arm" }));
    const arm = screen.getByRole("group", { name: "Arm capo" });
    commit(within(arm).getByLabelText("max_blocks"), "3");
    fireEvent.click(within(arm).getByRole("button", { name: "potter" }));
    expect(within(arm).queryByLabelText("max_blocks")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    const sent = onStart.mock.lastCall?.[0] as StartCheckinOptions;
    expect(sent.head_to_head?.arms).toEqual([
      { arm_key: "capo", optimization: { optimizer: "potter" } },
    ]);
  });
});
