"use client";
// Usage & limits — what this account has spent, how many runs it may hold at once, and how many
// campaigns it started today. Every figure is `/auth/quota-status` or `/machine-status` as served;
// the meters only draw them.

import { useState } from "react";
import { AccountFailure, AccountLoading, AccountSection } from "./AccountSection";
import { SegmentedControl, CommitInput, type Segment } from "@/components/ui";
import { cx } from "@/lib/cx";
import { fmtTokens, fmtUsd } from "@/lib/format";
import { useFetch } from "@/lib/hooks/useFetch";
import { useMachineStatus } from "@/lib/hooks/useMachineStatus";
import {
  ApiError,
  fetchQuotaStatus,
  postSetConcurrentCycles,
  type MachineStatusResponse,
  type QuotaStatus,
} from "@/lib/api";

// A limit above this many slots edits as a number instead of one button per value.
const SEGMENTED_MAX = 8;

export function AccountUsageTab() {
  const { data, error, kind } = useFetch(() => fetchQuotaStatus(), []);
  // A save re-reads in place, so the pane does not blank between the press and the answer.
  const [fresh, setFresh] = useState<QuotaStatus | null>(null);
  const machine = useMachineStatus();
  const quota = fresh ?? data;

  if (error && quota === null) return <AccountFailure kind={kind} subject="your usage" />;
  if (quota === null) return <AccountLoading subject="your usage" />;
  return (
    <>
      <SpendSection quota={quota} />
      <RunsSection
        quota={quota}
        machine={machine}
        onSaved={() => fetchQuotaStatus().then(setFresh)}
      />
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
  );
}

function Meter({ used, cap, tone }: { used: number; cap: number; tone?: "warn" }) {
  const share = cap > 0 ? Math.min(1, used / cap) : 1;
  return (
    <div className={cx("account-meter", tone === "warn" && "warn")} aria-hidden="true">
      <div className="account-meter-fill" style={{ width: `${share * 100}%` }} />
    </div>
  );
}

function SpendSection({ quota }: { quota: QuotaStatus }) {
  // Billed tokens with no resolvable rate, so the $ figure is a floor and the token ceiling is the
  // one binding. Same condition and same words as the run strip's pill (`shell/RemoteControl.tsx`).
  const blind = quota.spend_unpriced_tokens > 0;
  const usdCap = quota.spend_budget_usd_total;
  const tokenCap = quota.token_budget_total;
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
            {blind ? "≥ " : ""}
            {fmtUsd(quota.spend_used_total_usd)}
            <span className="account-figure-of">
              {usdCap === null ? " no ceiling" : ` of ${fmtUsd(usdCap)}`}
            </span>
          </span>
          {usdCap !== null ? (
            <Meter used={quota.spend_used_total_usd} cap={usdCap} tone={blind ? "warn" : undefined} />
          ) : null}
          {blind ? (
            <span className="account-warn">
              ⚠ USD cap inactive — {fmtTokens(quota.spend_unpriced_tokens)} billed with no
              resolvable rate, so this figure undercounts. The token ceiling is the one holding.
            </span>
          ) : null}
        </div>
        <div className="account-wallet-cell">
          <span className="account-kicker">Tokens</span>
          <span className="account-figure">
            {fmtTokens(quota.tokens_used_total)}
            <span className="account-figure-of">
              {tokenCap === null ? " no ceiling" : ` of ${fmtTokens(tokenCap)}`}
            </span>
          </span>
          {tokenCap !== null ? <Meter used={quota.tokens_used_total} cap={tokenCap} /> : null}
        </div>
      </div>
    </AccountSection>
  );
}

// One pip per slot; a slot at or past `open` is one the machine is holding back.
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
  onSaved,
}: {
  quota: QuotaStatus;
  machine: MachineStatusResponse | null;
  onSaved: () => Promise<void>;
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
                running={machine.running}
                queued={0}
                open={machine.capacity}
                label={`${machine.running} of ${machine.ceiling} slots running`}
              />
              <span className="account-runs-text">
                <strong>{machine.running}</strong> running · <strong>{machine.queued}</strong>{" "}
                waiting · <strong>{machine.ceiling}</strong> slots
                {machine.capacity < machine.ceiling ? (
                  <span className="account-warn">
                    {" "}
                    — admitting {machine.capacity} while the provider throttle is saturated
                  </span>
                ) : null}
              </span>
            </>
          )}
        </div>
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
      <LimitControl quota={quota} machine={machine} onSaved={onSaved} />
    </AccountSection>
  );
}

function refusal(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "concurrency_above_machine") {
      return "That is more than this machine runs at once, so it could never bind.";
    }
    if (err.code === "concurrency_set_by_host") {
      return "Whoever runs this server sets this account's limit.";
    }
    if (err.status === 404 || err.status === 403) {
      return "This session may not change spend limits.";
    }
  }
  return "The server did not take the change. Nothing was written; try again.";
}

function LimitControl({
  quota,
  machine,
  onSaved,
}: {
  quota: QuotaStatus;
  machine: MachineStatusResponse | null;
  onSaved: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
    setBusy(true);
    setError(null);
    try {
      await postSetConcurrentCycles(next);
    } catch (e) {
      setError(refusal(e));
      setBusy(false);
      return;
    }
    try {
      await onSaved();
    } catch {
      setError("Saved, but the new limit could not be read back. Reopen this pane to see it.");
    }
    setBusy(false);
  };

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
      {error ? (
        <p className="account-failure" role="alert">
          {error}
        </p>
      ) : null}
      {ceiling !== null && limit > ceiling ? (
        <p className="account-warn">
          This account&rsquo;s limit of {limit} is above the machine&rsquo;s {ceiling}, so the
          machine is what binds.
        </p>
      ) : null}
      <p className="account-note">
        Lowering it stops nothing already running; it refuses the next launch. The machine&rsquo;s
        slots are set in the server environment (<code>MACHINE_RUN_CAPACITY</code>) by whoever
        runs it and cannot be changed here.
      </p>
    </div>
  );
}
