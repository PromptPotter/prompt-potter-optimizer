"use client";

import { invalidateReads } from "@/lib/read-cache";
import { AccountSection } from "./AccountSection";
import {
  Button,
  SegmentedControl,
  CommitInput,
  DataTable,
  FailureLine,
  ReadState,
  type Column,
  type Segment,
} from "@/components/ui";
import { cx } from "@/lib/cx";
import { allowanceCaveats, billText, couponFaceText, couponLeftText } from "@/lib/derivations";
import { cssPct, fmtAgo } from "@/lib/format";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { useMachineStatus } from "@/lib/hooks/useMachineStatus";
import { useCommand, type CommandFailure } from "@/lib/hooks/useCommand";
import { useWorkspace } from "@/lib/workspace";
import {
  couponRead,
  quotaRead,
  postCommand,
  type CouponStatus,
  type MachineSlot,
  type MachineStatusResponse,
  type QuotaStatus,
} from "@/lib/api";

const SEGMENTED_MAX = 8;

export function AccountUsageTab() {
  const read = useRead(quotaRead(), { auth: true });
  const coupon = useRead(couponRead(), { auth: true });
  const machine = readyData(useMachineStatus());
  return (
    <>
      <ReadState read={coupon} subject="your allowance">
        {(c) => <AllowanceSection coupon={c} />}
      </ReadState>
      <ReadState read={read} subject="your usage">
        {(quota) => (
          <>
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
    </>
  );
}

function Meter({ share, warn = false }: { share: number; warn?: boolean }) {
  return (
    <div className={cx("account-meter", warn && "warn")} aria-hidden="true">
      <div className="account-meter-fill" style={{ width: cssPct(share) }} />
    </div>
  );
}

function allowanceLede(c: CouponStatus): string {
  if (c.host) {
    return "This account runs the box, so its sends on the box's own provider keys are not metered.";
  }
  if (c.void) {
    return "Retired: this account holds a key of its own, so the host's keys no longer send for it.";
  }
  if (c.exhausted) {
    return "Used up: nothing more is sent on the host's keys. Add a key of your own to keep running — every result stays readable.";
  }
  return "What the host's provider keys may pay for this account over its life. A key of your own sends instead, billed to you, and never draws it.";
}

function AllowanceSection({ coupon }: { coupon: CouponStatus }) {
  const { openAccount } = useWorkspace();
  const host = coupon.host_key_spend;
  const own = coupon.own_key_spend;
  // A retired coupon serves `exhausted` too, but nothing is wrong: the account sends on its own keys.
  const spent = !coupon.host && !coupon.void && coupon.exhausted;
  const held = coupon.keys.length;
  return (
    <AccountSection
      title="Host allowance"
      lede={allowanceLede(coupon)}
      aside={<Button onClick={() => openAccount("keys")}>API keys</Button>}
    >
      <div className="account-wallet">
        <div className="account-wallet-cell">
          <span className="account-kicker">On the host&rsquo;s keys</span>
          <span className="account-figure">
            {billText(host.billed_usd, host.bill_is_floor)}
            <span className="account-figure-of"> {couponFaceText(coupon)}</span>
          </span>
          {coupon.used_share !== null ? <Meter share={coupon.used_share} warn={spent} /> : null}
          {coupon.host ? null : (
            <span className={spent ? "account-warn" : "account-note"}>
              Left: {couponLeftText(coupon)}
            </span>
          )}
          {allowanceCaveats(host).map((line) => (
            <span key={line} className="account-warn">
              {line}
            </span>
          ))}
        </div>
        <div className="account-wallet-cell">
          <span className="account-kicker">On your own keys</span>
          <span className="account-figure">{billText(own.billed_usd, own.bill_is_floor)}</span>
          <span className="account-note">
            {held === 0
              ? "No key held."
              : `${held} key${held === 1 ? "" : "s"} held. Billed to you, never drawn from the allowance.`}
          </span>
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
