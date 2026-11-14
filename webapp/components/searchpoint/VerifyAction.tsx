import { useState } from "react";
import type { SelectedCandidate } from "@/lib/types";
import type { CyclePath } from "@/lib/ids";
import { commands, cycleAddress, mintCommandId, type VerifyStrategy } from "@/lib/api/commands";
import {
  READING_STATE_INFO,
  VERIFY_STRATEGY_LABELS,
  VERIFY_WORDS,
  type CommandResponses,
} from "@/lib/api/types";
import { wordKeys } from "@/lib/api/words";
import { useCommand } from "@/lib/hooks/useCommand";
import { useVerify } from "@/lib/hooks/useVerify";
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
  const cmd = useCommand<"verify-candidate">("verify-candidate");
  const stop = useCommand<"cancel">("cancel");
  const [acting, setActing] = useState<string | null>(null);
  const [strategy, setStrategy] = useState<VerifyStrategy>("random");
  const [samples, setSamples] = useState("");
  const [saturated, setSaturated] = useState<Saturated | null>(null);
  const [prevArm, setPrevArm] = useState(candidate.arm);
  if (candidate.arm !== prevArm) {
    setPrevArm(candidate.arm);
    setSaturated(null);
  }
  const sending = cmd.pending !== null;
  const { readings, passes, unit } = useVerify(path);

  if (!path || !candidate.label) return null;

  const reading = readings.get(candidate.arm) ?? null;
  const last = passes.get(candidate.arm) ?? null;
  const flying = last?.state === "running" ? last : null;
  const busy = sending || flying !== null;

  function run() {
    if (!path) return;
    setSaturated(null);
    const commandId = mintCommandId();
    setActing(commandId);
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
        (answer) => setSaturated("kind" in answer ? answer : null),
      )
      .finally(() => setActing(null));
  }

  return (
    <div className="verify-action">
      <div className="verify-action-row">
        <HoverCard content="Re-score this candidate on search cells it has never been measured on.">
          <Button onClick={run} disabled={busy}>
            {busy ? "Verifying…" : `Verify ${candidate.label}`}
          </Button>
        </HoverCard>
        {acting && (
          <HoverCard content="Stop at the next cell; what it measured is read and kept.">
            <Button
              onClick={() => void stop.run("cancel", () => commands.cancel({ command_id: acting }))}
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
      </div>
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
      {!busy && saturated && (
        <p className="verify-action-note">{filled(VERIFY_WORDS.saturated, saturated)}</p>
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
