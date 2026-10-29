"use client";
import { optimizerRosterRead, type StartCheckinOptions } from "@/lib/api";
import { useRead } from "@/lib/hooks/useRead";
import { Chip, ChipGroup, CommitInput, ReadState } from "@/components/ui";

// The server's `Arm` pattern: an id it would refuse is kept on screen, never sent.
const ARM_ID = /^[A-Za-z0-9_-]{1,64}$/;

export type HeadToHeadDraft = { id: string; optimizers: string[] };

export const NO_HEAD_TO_HEAD: HeadToHeadDraft = { id: "", optimizers: [] };

export function headToHeadLabel(h2h: HeadToHeadDraft): string {
  return h2h.id && h2h.optimizers.length
    ? `${h2h.id} · ${h2h.optimizers.join(" vs ")}`
    : "one campaign";
}

// One arm per optimizer, keyed by its name; the bounds step's budget binds each arm on its own book.
export function startOptions(launch: StartCheckinOptions, h2h: HeadToHeadDraft): StartCheckinOptions {
  if (!h2h.id || h2h.optimizers.length === 0) return launch;
  return {
    head_to_head: {
      head_to_head_id: h2h.id,
      arms: h2h.optimizers.map((name) => ({ arm_key: name, optimization: { optimizer: name } })),
      budget: launch.budget ?? {},
    },
  };
}

export function HeadToHeadSetupSection({
  value,
  onChange,
}: {
  value: HeadToHeadDraft;
  onChange: (next: HeadToHeadDraft) => void;
}) {
  const rosterRead = useRead(optimizerRosterRead());
  return (
    <>
      <p className="bnode-role">
        Start one campaign per optimizer picked here, all on this origin and the bounds below, each
        bound on its own. Only the optimizer differs, so their results compare under control.
      </p>
      <label>
        Head-to-head id{" "}
        <CommitInput
          value={value.id}
          validate={(draft) => draft === "" || ARM_ID.test(draft)}
          onCommit={(id) => onChange({ ...value, id })}
          placeholder="e.g. capo-vs-potter"
        />
      </label>
      <ReadState read={rosterRead} subject="the optimizers this install runs" className="mech-empty">
        {(roster) => (
          <ChipGroup label="Arms">
            {roster.optimizers.map(({ name }) => {
              const on = value.optimizers.includes(name);
              return (
                <Chip
                  key={name}
                  on={on}
                  onClick={() =>
                    onChange({
                      ...value,
                      optimizers: on
                        ? value.optimizers.filter((o) => o !== name)
                        : [...value.optimizers, name],
                    })
                  }
                >
                  {name}
                </Chip>
              );
            })}
          </ChipGroup>
        )}
      </ReadState>
    </>
  );
}
