import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { KnobRow, OptimizerSetup, StartCheckinOptions } from "@/lib/api";
import { clearReadCache } from "@/lib/read-cache";
import { AuthProvider } from "@/lib/state/auth";
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

function setupOf(node: string, key: string, value: number): OptimizerSetup {
  return {
    main: [{ node, knob: key }],
    knobs: [{ node, kind: "proposer", knobs: [knob(key, value)] }],
    prompts: {},
    arms_per_round: 4,
    proposers: [],
    pools: [],
  };
}

// What `/drafts/{id}/optimizer-setup` serves for each optimizer over the one draft.
const SETUPS: Record<string, OptimizerSetup> = {
  capo: setupOf("blocks", "max_blocks", 8),
  potter: setupOf("l1_generate", "temperature_steps", 3),
};

function Harness({ base, onStart }: { base: string; onStart: (sent: StartCheckinOptions) => void }) {
  const [h2h, setH2h] = useState<HeadToHeadDraft>({ id: "duel", arms: [] });
  return (
    <AuthProvider>
      <HeadToHeadArms
        value={h2h}
        onChange={setH2h}
        optimizers={["capo", "potter"]}
        base={base}
        draftId="d_1"
      />
      <button type="button" onClick={() => onStart(startOptions({ budget: { usd: 2 } }, h2h))}>
        Start
      </button>
    </AuthProvider>
  );
}

function commit(input: HTMLElement, text: string) {
  fireEvent.change(input, { target: { value: text } });
  fireEvent.keyDown(input, { key: "Enter" });
}

describe("HeadToHeadArms", () => {
  beforeEach(() => {
    clearReadCache();
    // Every other read, the session probe among them, answers as an anonymous visitor's would.
    vi.stubGlobal("fetch", (url: string) => {
      const setup = SETUPS[new URL(url, "http://draft").searchParams.get("optimizer") ?? ""];
      return Promise.resolve(
        setup ? new Response(JSON.stringify(setup)) : new Response(null, { status: 401 }),
      );
    });
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("sends one optimizer twice, apart by its own knob, whichever optimizer the setup runs", async () => {
    const onStart = vi.fn();
    render(<Harness base="potter" onStart={onStart} />);
    const add = screen.getByRole("button", { name: "Add arm" });
    fireEvent.click(add);
    fireEvent.click(add);
    for (const name of ["Arm potter", "Arm potter-2"]) {
      fireEvent.click(within(screen.getByRole("group", { name })).getByRole("button", { name: "capo" }));
    }
    commit(within(screen.getByRole("group", { name: "Arm potter" })).getByLabelText("Arm key"), "capo");
    commit(within(screen.getByRole("group", { name: "Arm potter-2" })).getByLabelText("Arm key"), "capo-b5");
    const b5 = screen.getByRole("group", { name: "Arm capo-b5" });
    commit(await within(b5).findByLabelText("max_blocks"), "5");
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

    // A knob set back to its optimizer's value leaves the arm: it then runs what the sibling runs.
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
    render(<Harness base="capo" onStart={onStart} />);
    fireEvent.click(screen.getByRole("button", { name: "Add arm" }));
    fireEvent.click(within(screen.getByRole("group", { name: "Arm capo" })).getByRole("button", { name: "Remove" }));
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(onStart).toHaveBeenLastCalledWith({ budget: { usd: 2 } });
  });

  it("drops an arm's knobs when it switches optimizer: they were the other one's", async () => {
    const onStart = vi.fn();
    render(<Harness base="capo" onStart={onStart} />);
    fireEvent.click(screen.getByRole("button", { name: "Add arm" }));
    const arm = screen.getByRole("group", { name: "Arm capo" });
    commit(await within(arm).findByLabelText("max_blocks"), "3");
    fireEvent.click(within(arm).getByRole("button", { name: "potter" }));
    await within(arm).findByLabelText("temperature_steps");
    expect(within(arm).queryByLabelText("max_blocks")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    const sent = onStart.mock.lastCall?.[0] as StartCheckinOptions;
    expect(sent.head_to_head?.arms).toEqual([
      { arm_key: "capo", optimization: { optimizer: "potter" } },
    ]);
  });
});
