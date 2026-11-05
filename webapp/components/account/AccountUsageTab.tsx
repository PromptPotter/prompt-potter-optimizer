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
import { allowanceCaveats, billText } from "@/lib/derivations";
import {
  ACCOUNT_TAB_INFO,
  SLOT_BOUND_WORDS,
  type CouponStatus,
  type MachineSlot,
  type MachineStatusResponse,
  type QuotaStatus,
} from "@/lib/api/types";
import { cssPct, filled, fmtAgo } from "@/lib/format";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { useMachineStatus } from "@/lib/hooks/useMachineStatus";
import { useCommand } from "@/lib/hooks/useCommand";
import { useWorkspace } from "@/lib/workspace";
import { couponRead, quotaRead, postCommand } from "@/lib/api";

const SEGMENTED_MAX = 8;

export function AccountUsageTab() {
  const read = useRead(quotaRead());
  const coupon = useRead(couponRead());
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

function AllowanceSection({ coupon }: { coupon: CouponStatus }) {
  const { open } = useWorkspace();
  const own = coupon.own_key_spend;
  const said = coupon.headline;
  const held = coupon.routes.filter((r) => r.key !== null).length;
  return (
    <AccountSection
      title="Host allowance"
      lede={said.detail}
      aside={<Button onClick={() => open("account", "keys")}>{ACCOUNT_TAB_INFO.keys.label}</Button>}
    >
      <div className="account-wallet">
        <div className="account-wallet-cell">
          <span className="account-kicker">On the host&rsquo;s keys</span>
          <span className="account-figure">{said.value}</span>
          {coupon.used_share !== null ? (
            <Meter share={coupon.used_share} warn={said.tone === "warn"} />
          ) : null}
          {allowanceCaveats(coupon.host_key_spend).map((line) => (
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
                {machine.bound !== null ? (
                  <span className="account-warn"> — {filled(SLOT_BOUND_WORDS[machine.bound], machine)}</span>
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
      <LimitControl quota={quota} />
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

function LimitControl({ quota }: { quota: QuotaStatus }) {
  const cmd = useCommand<"set-concurrent-cycles">("account-run-limit");
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
    await cmd.run("set-concurrent-cycles", () =>
      postCommand("set-concurrent-cycles", { max_concurrent_cycles: next }),
    );
  };

  const busy = cmd.pending !== null;
  const ceiling = quota.ceiling;
  return (
    <div className="account-limit">
      <div className="account-limit-control">
        <span className="account-kicker">Account limit</span>
        {ceiling <= SEGMENTED_MAX ? (
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
      {quota.bound !== null ? (
        <p className="account-warn">{filled(SLOT_BOUND_WORDS[quota.bound], quota)}</p>
      ) : null}
      <p className="account-note">
        Lowering it stops nothing already running; it refuses the next launch. The machine&rsquo;s
        slots are set in the server environment (<code>MACHINE_RUN_CAPACITY</code>) by whoever
        runs it and cannot be changed here.
      </p>
    </div>
  );
}
