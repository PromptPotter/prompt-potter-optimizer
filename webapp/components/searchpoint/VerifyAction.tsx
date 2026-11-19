import { useState } from "react";
import type { SelectedCandidate } from "@/lib/types";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";
import { commands, cycleAddress, mintCommandId, type VerifyStrategy } from "@/lib/api/commands";
import {
  PASS_REFUSAL_INFO,
  READING_STATE_INFO,
  VERIFY_STRATEGY_LABELS,
  VERIFY_WORDS,
  type CommandResponses,
} from "@/lib/api/types";
import { wordKeys } from "@/lib/api/words";
import { useCommand } from "@/lib/hooks/useCommand";
import { useVerify } from "@/lib/hooks/useVerify";
import { useCycleAt } from "@/lib/state/lineage";
import { filled, unitCount } from "@/lib/format";
import {
  Button,
  Chip,
  CommitInput,
  FailureLine,
  HoverCard,
  IconMore,
  Menu,
  MenuRadioGroup,
  MenuSep,
} from "@/components/ui";
import { VerifyReading } from "./VerifyReading";

// The answer of a verify that bought nothing: every fresh cell was already measured.
type Saturated = Extract<CommandResponses["verify-candidate"], { kind: "verify_saturated" }>;

const STRATEGIES = wordKeys(VERIFY_STRATEGY_LABELS).map((value) => ({
  value,
  label: VERIFY_STRATEGY_LABELS[value],
}));

export function VerifyAction({
  candidate,
  path,
}: {
  candidate: SelectedCandidate;
  path: CyclePath | null;
}) {
  const scope = path && encodeCyclePath(path);
  const cmd = useCommand<"verify-candidate">("verify-candidate", scope);
  const stop = useCommand<"cancel">("cancel", scope);
  // A press is the candidate's it was pressed on: another cycle or candidate paints none of it.
  const here = `${scope}\n${candidate.arm}`;
  const [acting, setActing] = useState<{ at: string; id: string } | null>(null);
  const [strategy, setStrategy] = useState<VerifyStrategy>("random");
  const [samples, setSamples] = useState("");
  const [saturated, setSaturated] = useState<{ at: string; answer: Saturated } | null>(null);
  const stopping = acting?.at === here ? acting.id : null;
  const said = saturated?.at === here ? saturated.answer : null;
  const sending = cmd.pending !== null;
  const { readings, passes, unit } = useVerify(path);
  // The served admission of the cycle it files on, the one the command is refused by, its reader
  // joined (`read_only`: it reads the passes and presses none); null under a moment.
  const admission = useCycleAt(path)?.facts.verify ?? null;
  const offered = admission !== null && admission.refusal !== "read_only";

  if (!path || !candidate.label) return null;

  const reading = readings.get(candidate.arm) ?? null;
  const last = passes.get(candidate.arm) ?? null;
  const flying = last?.state === "running" ? last : null;
  const busy = sending || flying !== null;

  function run() {
    if (!path) return;
    setSaturated(null);
    const commandId = mintCommandId();
    setActing({ at: here, id: commandId });
    void cmd
      .run(
        "verify-candidate",
        () =>
          // `samples` omitted is the server's count (`verify.py::derive_verify_samples`).
          commands["verify-candidate"](
            {
              ...cycleAddress(path),
              candidate_id: candidate.arm,
              strategy,
              ...(samples === "" ? {} : { samples: Number(samples) }),
            },
            commandId,
          ),
        (answer) => setSaturated("kind" in answer ? { at: here, answer } : null),
      )
      .finally(() => setActing((a) => (a?.id === commandId ? null : a)));
  }

  return (
    <div className="verify-action">
      {offered ? <div className="verify-action-row">
        <HoverCard
          content={
            admission.refusal
              ? PASS_REFUSAL_INFO[admission.refusal]
              : "Re-score this candidate on search cells it has never been measured on."
          }
        >
          <Button onClick={run} disabled={busy || !admission.can_run}>
            {busy ? "Verifying…" : `Verify ${candidate.label}`}
          </Button>
        </HoverCard>
        {stopping && (
          <HoverCard content="Stop at the next cell; what it measured is read and kept.">
            <Button
              onClick={() => void stop.run("cancel", () => commands.cancel({ command_id: stopping }))}
              disabled={stop.pending !== null}
            >
              Stop
            </Button>
          </HoverCard>
        )}
        <Menu
          align="left"
          renderTrigger={({ open, toggle }) => (
            <Chip
              icon
              on={open || strategy !== "random" || samples !== ""}
              expanded={open}
              ariaLabel="Verify options"
              title="Which cells a verify picks, and how many"
              onClick={toggle}
            >
              <IconMore />
            </Chip>
          )}
        >
          {() => (
            <>
              <MenuRadioGroup
                label="Fresh cells picked"
                value={strategy}
                options={STRATEGIES}
                onChange={setStrategy}
              />
              <MenuSep />
              <label className="verify-samples">
                <span>cells</span>
                <CommitInput
                  type="text"
                  inputMode="numeric"
                  value={samples}
                  placeholder="derived"
                  aria-label="Fresh cells to measure; blank lets the round budget decide"
                  validate={(d) => d === "" || /^[1-9]\d*$/.test(d)}
                  onCommit={setSamples}
                />
              </label>
              <p className="verify-samples-note">
                Blank takes the count from the round budget and how long this cycle has gone
                unverified. A larger one is refused.
              </p>
            </>
          )}
        </Menu>
      </div> : null}
      {busy && (
        <p className="verify-action-note">
          {flying && unit
            ? `Measuring ${flying.arm.label} on ${unitCount(flying.rows, unit)} it has never seen, ${flying.scored} scored.`
            : `Measuring ${candidate.label} on cells it has never seen.`}{" "}
          This mints no round, so the cycle&rsquo;s own series does not move.
        </p>
      )}
      <FailureLine failure={cmd.failure} />
      <FailureLine failure={stop.failure} />
      {!busy && said && (
        <p className="verify-action-note">{filled(VERIFY_WORDS.saturated, said)}</p>
      )}
      {!busy && last?.failed_as != null && (
        <p className="verify-action-note error">
          The last verify failed: {READING_STATE_INFO[last.failed_as].sentence}
        </p>
      )}
      {reading && unit && <VerifyReading reading={reading} unit={unit} />}
    </div>
  );
}
