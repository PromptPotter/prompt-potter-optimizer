"use client";
import { Fragment, type ReactNode } from "react";
import { CopyButton, HoverCard } from "@/components/ui";
import { useFetch } from "@/lib/hooks/useFetch";
import { fetchCampaignStorage } from "@/lib/api";
import { cx } from "@/lib/cx";
import { fmtBytes } from "@/lib/format";

// The ONE hover surface for every sidebar row. Three tiers, read top-down: WHAT the row is
// (name, state, one line), the few NUMBERS an operator scans for, then the IDs and dates they
// copy out. Rows hand in served values already formatted; the card lays them out and nothing else.
//
// The card is reachable (see `HoverCard`), so what is in it selects by drag, and the copy button
// hands the same values over as JSON — both read the SAME lists, so the payload cannot claim
// anything the card does not show.

export interface RowStat {
  label: string;
  value: string;
  // A second line under the value — which floor a Δ is against, what a figure leaves out.
  sub?: string;
  className?: string;
}

export interface RowCardFacts {
  title: string;
  // The row's run-state or verdict word — always a word, never colour alone.
  state?: string | null;
  tags?: string[];
  lede: string;
  stats: RowStat[];
  // A served caveat that makes a number above unreadable as it stands.
  caveat?: ReactNode;
  facts: [string, string][];
  // Present ⇒ the on-disk breakdown is fetched (lazily — the card mounts only while open).
  campaignId?: string;
}

const snake = (label: string) => label.toLowerCase().replace(/[^a-z0-9]+/g, "_");

export function RowHoverCard({ card, children }: { card: RowCardFacts; children: ReactNode }) {
  return (
    <HoverCard className="rowhover-shell" content={<RowCardBody card={card} />}>
      {children}
    </HoverCard>
  );
}

function RowCardBody({ card }: { card: RowCardFacts }) {
  const { campaignId } = card;
  const { data, error } = useFetch(
    campaignId != null ? (signal) => fetchCampaignStorage(campaignId, signal) : null,
    [campaignId ?? null],
  );

  // "On disk" is the whole; the operator axis is Dataset / Connector / Loop, and Loop =
  // State + Trace + History + Reports. The flag indents a Loop leaf.
  const loop = data && data.state_bytes + data.trace_bytes + data.history_bytes + data.reports_bytes;
  const sizes: [string, number | undefined, boolean?][] = [
    ["Dataset", data?.dataset_bytes],
    ["Connector", data?.connector_bytes],
    ["Loop", loop ?? undefined],
    ["State", data?.state_bytes, true],
    ["Trace", data?.trace_bytes, true],
    ["History", data?.history_bytes, true],
    ["Reports", data?.reports_bytes, true],
  ];
  const size = (n: number | undefined) => (error ? "—" : data ? fmtBytes(n ?? 0) : "…");

  const payload = {
    name: card.title,
    ...(card.state ? { state: card.state } : {}),
    ...Object.fromEntries(card.stats.map((s) => [snake(s.label), s.value])),
    ...Object.fromEntries(card.facts.map(([k, v]) => [snake(k), v])),
    ...(data
      ? {
          on_disk: {
            total: size(data.on_disk_bytes),
            ...Object.fromEntries(sizes.map(([k, n]) => [snake(k), size(n)])),
          },
        }
      : {}),
  };

  return (
    <div className="rowhover">
      <header className="rowhover-head">
        <div className="rowhover-titleline">
          <span className="rowhover-title">{card.title}</span>
          {card.tags?.map((t) => (
            <span key={t} className="rowhover-tag">
              {t}
            </span>
          ))}
          {card.state && <span className="rowhover-state">{card.state}</span>}
        </div>
        <CopyButton title="Copy these details as JSON" data={payload} />
      </header>
      <p className="rowhover-lede">{card.lede}</p>

      {card.stats.length > 0 && (
        <dl className="rowhover-stats">
          {card.stats.map((s) => (
            <div key={s.label} className="rowhover-stat">
              <dt>{s.label}</dt>
              <dd className={cx("rowhover-stat-value", s.className)}>{s.value}</dd>
              {s.sub && <dd className="rowhover-stat-sub">{s.sub}</dd>}
            </div>
          ))}
        </dl>
      )}
      {card.caveat && <div className="rowhover-caveat">{card.caveat}</div>}

      <dl className="rowhover-facts">
        {card.facts.map(([label, value]) => (
          <Fragment key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </Fragment>
        ))}
      </dl>

      {campaignId && (
        <details className="rowhover-disk">
          <summary>
            <span>On disk</span>
            <span className="rowhover-disk-total">{size(data?.on_disk_bytes)}</span>
          </summary>
          <dl className="rowhover-facts">
            {sizes.map(([label, n, indent]) => (
              <Fragment key={label}>
                <dt data-indent={indent || undefined}>{label}</dt>
                <dd>{size(n)}</dd>
              </Fragment>
            ))}
          </dl>
          <p className="rowhover-note">delete --keep-results spares Reports + loop trace</p>
        </details>
      )}
    </div>
  );
}
