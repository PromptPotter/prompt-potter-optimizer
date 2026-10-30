"use client";

import { invalidateReads } from "@/lib/read-cache";
import { AccountSection } from "./AccountSection";
import {
  SegmentedControl,
  CommitInput,
  DataTable,
  FailureLine,
  ReadState,
  type Column,
  type Segment,
} from "@/components/ui";
import { cx } from "@/lib/cx";
import { allowanceCaveats, billText } from "@/lib/derivations";
import { cssPct, fmtAgo, fmtTokens, fmtUsd } from "@/lib/format";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { useMachineStatus } from "@/lib/hooks/useMachineStatus";
import { useCommand, type CommandFailure } from "@/lib/hooks/useCommand";
import {
  quotaRead,
  postCommand,
  type MachineSlot,
  type MachineStatusResponse,
  type QuotaStatus,
} from "@/lib/api";

const SEGMENTED_MAX = 8;

export function AccountUsageTab() {
  const read = useRead(quotaRead(), { auth: true });
  const machine = readyData(useMachineStatus());
  return (
    <ReadState read={read} subject="your usage">
      {(quota) => (
        <>
          <SpendSection quota={quota} />
          <RunsSection quota={quota} machine={machine} />
          <AccountSection
            title="Campaigns today"
            lede="An abuse limit, not an allowance. It resets at UTC midnight."
          >
            <p className="account-figure">
              {quota.campaigns_today}
              <span className="account-figure-of"> of {quota.max_campaigns_per_day}</span>
            </p>
          </AccountSection>
        </>
      )}
    </ReadState>
  );
}

function Meter({ share, warn = false }: { share: number; warn?: boolean }) {
  return (
    <div className={cx("account-meter", warn && "warn")} aria-hidden="true">
      <div className="account-meter-fill" style={{ width: cssPct(share) }} />
    </div>
  );
}

function SpendSection({ quota }: { quota: QuotaStatus }) {
  const lifetime = quota.spend_lifetime;
  const blind = lifetime.bill_is_floor;
  const usdCap = quota.spend_budget_usd_total;
  const tokenCap = quota.token_budget_total;
  const usdShare = quota.spend_budget_used_share;
  const tokenShare = quota.token_budget_used_share;
  const metered = usdCap !== null || tokenCap !== null;
  return (
    <AccountSection
      title="Spend"
      lede={
        metered
          ? "Lifetime, across every campaign this account ran. A launch the rest cannot cover is refused whole."
          : "Lifetime, across every campaign this account ran. No ceiling: this account runs on the server's own provider key."
      }
    >
      <div className="account-wallet">
        <div className="account-wallet-cell">
          <span className="account-kicker">Model spend</span>
          <span className="account-figure">
            {billText(lifetime.billed_usd, blind)}
            <span className="account-figure-of">
              {usdCap === null ? " no ceiling" : ` of ${fmtUsd(usdCap)}`}
            </span>
          </span>
          {usdShare !== null ? <Meter share={usdShare} warn={blind} /> : null}
          {allowanceCaveats(lifetime).map((line) => (
            <span key={line} className="account-warn">
              {line}
            </span>
          ))}
        </div>
        <div className="account-wallet-cell">
          <span className="account-kicker">Tokens</span>
          <span className="account-figure">
            {fmtTokens(quota.tokens_used_total)}
            <span className="account-figure-of">
              {tokenCap === null ? " no ceiling" : ` of ${fmtTokens(tokenCap)}`}
            </span>
          </span>
          {tokenShare !== null ? <Meter share={tokenShare} /> : null}
        </div>
      </div>
    </AccountSection>
  );
}

function Slots({
  total,
  running,
  queued,
  open,
  label,
}: {
  total: number;
  running: number;
  queued: number;
  open: number;
  label: string;
}) {
  return (
    <span className="account-slots" role="img" aria-label={label}>
      {Array.from({ length: total }, (_, i) => (
        <span
          key={i}
          className={cx(
            "account-slot",
            i < running && "running",
            i >= running && i < running + queued && "queued",
            i >= open && "held",
          )}
        />
      ))}
    </span>
  );
}

function RunsSection({
  quota,
  machine,
}: {
  quota: QuotaStatus;
  machine: MachineStatusResponse | null;
}) {
  const limit = quota.max_concurrent_cycles;
  const running = quota.concurrent_running;
  const queued = quota.concurrent_queued;
  const slots = Math.max(limit, running + queued);
  return (
    <AccountSection
      title="Runs at once"
      lede="How many campaigns this account holds at the same time. A queued launch counts as held; a launch past the limit is refused rather than queued."
    >
      <div className="account-runs">
        <div className="account-runs-row">
          <span className="account-kicker">This account</span>
          <Slots
            total={slots}
            running={running}
            queued={queued}
            open={slots}
            label={`${running} running, ${queued} queued, limit ${limit}`}
          />
          <span className="account-runs-text">
            <strong>{running}</strong> running · <strong>{queued}</strong> queued · limit{" "}
            <strong>{limit}</strong>
          </span>
        </div>
        <div className="account-runs-row">
          <span className="account-kicker">This machine</span>
          {machine === null ? (
            <span className="account-runs-text account-note">Reading machine occupancy…</span>
          ) : (
            <>
              <Slots
                total={machine.ceiling}
                running={machine.occupied}
                queued={0}
                open={machine.capacity}
                label={`${machine.occupied} of ${machine.ceiling} slots running`}
              />
              <span className="account-runs-text">
                <strong>{machine.occupied}</strong> running ·{" "}
                <strong>{machine.queued}</strong>{" "}
                waiting · <strong>{machine.ceiling}</strong> slots
                {machine.held_back !== null ? (
                  <span className="account-warn"> — {machine.held_back}</span>
                ) : null}
              </span>
            </>
          )}
        </div>
        {machine !== null && machine.running.length > 0 ? (
          <DataTable
            columns={SLOT_COLUMNS}
            rows={machine.running}
            getRowId={slotId}
            ariaLabel="What holds each running slot on this machine"
          />
        ) : null}
        <div className="account-legend" aria-hidden="true">
          <span>
            <span className="account-slot running" /> running
          </span>
          <span>
            <span className="account-slot queued" /> queued
          </span>
          <span>
            <span className="account-slot" /> free
          </span>
          <span>
            <span className="account-slot held" /> held back
          </span>
        </div>
      </div>
      <LimitControl quota={quota} machine={machine} />
    </AccountSection>
  );
}

const SLOT_COLUMNS: readonly Column<MachineSlot>[] = [
  { id: "what", label: "Running", width: "minmax(0,1fr)", cell: (s) => s.what },
  { id: "user", label: "Account", width: "minmax(0,12rem)", cell: (s) => s.user },
  {
    id: "since",
    label: "Since",
    width: "8rem",
    align: "end",
    cell: (s) => (s.started_at === null ? "starting" : fmtAgo(s.started_at)),
  },
];

function slotId(s: MachineSlot): string {
  return s.job_id;
}

// Only a flat denial needs a sentence here: the server words its own refusals, and 403 and 404 are one answer.
function refusal(f: CommandFailure): string | null {
  return f.kind === "denied" || f.kind === "gone"
    ? "This session may not change spend limits."
    : null;
}

function LimitControl({
  quota,
  machine,
}: {
  quota: QuotaStatus;
  machine: MachineStatusResponse | null;
}) {
  const cmd = useCommand<"set-concurrent-cycles">("account-run-limit", { describe: refusal });
  const limit = quota.max_concurrent_cycles;

  if (!quota.max_concurrent_cycles_writable) {
    return (
      <div className="account-limit readonly">
        <p className="account-note">
          <strong>Set by whoever runs this server.</strong> This account runs on their provider
          key, so they decide how many of its runs share the machine. Ask them to raise it.
        </p>
      </div>
    );
  }

  const save = async (next: number) => {
    if (next === limit || !Number.isInteger(next) || next < 1) return;
    await cmd.run(
      "set-concurrent-cycles",
      () => postCommand("set-concurrent-cycles", { max_concurrent_cycles: next }),
      () =>
      invalidateReads("quota"),
    );
  };

  const busy = cmd.pending !== null;
  const ceiling = machine?.ceiling ?? null;
  return (
    <div className="account-limit">
      <div className="account-limit-control">
        <span className="account-kicker">Account limit</span>
        {ceiling === null ? (
          <span className="account-note">Waiting for the machine ceiling…</span>
        ) : ceiling <= SEGMENTED_MAX ? (
          <SegmentedControl
            size="lg"
            ariaLabel="Campaigns this account may hold at once"
            value={String(limit)}
            onChange={(v) => void save(Number(v))}
            options={Array.from(
              { length: ceiling },
              (_, i): Segment<string> => ({
                value: String(i + 1),
                label: String(i + 1),
                disabled: busy,
              }),
            )}
          />
        ) : (
          <CommitInput
            className="account-limit-input"
            type="number"
            min={1}
            max={ceiling}
            value={String(limit)}
            disabled={busy}
            aria-label="Campaigns this account may hold at once"
            onCommit={(v) => void save(Number(v))}
          />
        )}
        {busy ? <span className="account-note">Saving…</span> : null}
      </div>
      <FailureLine failure={cmd.failure} />
      {quota.machine_binds !== null ? (
        <p className="account-warn">{quota.machine_binds}</p>
      ) : null}
      <p className="account-note">
        Lowering it stops nothing already running; it refuses the next launch. The machine&rsquo;s
        slots are set in the server environment (<code>MACHINE_RUN_CAPACITY</code>) by whoever
        runs it and cannot be changed here.
      </p>
    </div>
  );
}
