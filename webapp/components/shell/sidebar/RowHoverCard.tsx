"use client";
import { Fragment, useState, type ReactNode } from "react";
import { CopyButton, HoverCard } from "@/components/ui";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { fetchCampaignStorage, fetchConfigMap } from "@/lib/api";
import { cx } from "@/lib/cx";
import { declaredKnobs, type RowCardFacts } from "@/lib/derivations";
import { fmtBytes, fmtValue } from "@/lib/format";

// The ONE hover surface for every sidebar row. Three tiers, read top-down: WHAT the row is
// (name, state, one line), the few NUMBERS an operator scans for, then the IDs and dates they
// copy out. Rows hand in served values already formatted; the card lays them out and nothing else.
//
// The card is reachable (see `HoverCard`), so what is in it selects by drag, and the copy button
// hands the same values over as JSON — both read the SAME lists, so the payload cannot claim
// anything the card does not show.

const snake = (label: string) => label.toLowerCase().replace(/[^a-z0-9]+/g, "_");

export function RowHoverCard({ card, children }: { card: RowCardFacts; children: ReactNode }) {
  return (
    <HoverCard block className="rowhover-shell" content={<RowCardBody card={card} />}>
      {children}
    </HoverCard>
  );
}

function RowCardBody({ card }: { card: RowCardFacts }) {
  const { campaignId, settings } = card;
  const read = useRead(
    campaignId != null
      ? { key: campaignId, fetch: (signal) => fetchCampaignStorage(campaignId, signal) }
      : null,
    { surface: "campaign-storage" },
  );
  const data = readyData(read);
  const error = read.status === "failed";
  // The declared config is a second read, and most hovers never ask for it — so it waits for the
  // fold. The latch stays true once opened: closing it again must not throw the answer away.
  const [configAsked, setConfigAsked] = useState(false);
  const configRead = useRead(
    campaignId != null && configAsked
      ? { key: campaignId, fetch: (signal) => fetchConfigMap(campaignId, signal) }
      : null,
    { surface: "campaign-config-map" },
  );
  const configMap = readyData(configRead);
  const knobs = configMap ? declaredKnobs(configMap) : null;

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
    ...(settings === undefined
      ? {}
      : {
          runs_with: settings
            ? settings.map((s) => ({
                node: s.node,
                key: s.key,
                value: s.value,
                source: s.source,
              }))
            : "pipeline unreadable",
        }),
    ...(knobs
      ? { declared_config: Object.fromEntries(knobs.map((k) => [k.path, fmtValue(k.value)])) }
      : {}),
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

      {settings !== undefined && (
        <section>
          <p className="rowhover-heading">Runs with</p>
          {settings ? (
            <dl className="rowhover-facts">
              {settings.map((s) => (
                <Fragment key={`${s.node}.${s.key}`}>
                  <dt>
                    {s.node} · {s.key}
                  </dt>
                  <dd>
                    {s.value}
                    <span className="rowhover-source">{s.source}</span>
                  </dd>
                </Fragment>
              ))}
            </dl>
          ) : (
            <p className="rowhover-note">
              The root pipeline did not resolve, so what this campaign runs with is unknown.
            </p>
          )}
        </section>
      )}

      {campaignId && (
        <details className="rowhover-fold" onToggle={() => setConfigAsked(true)}>
          <summary>
            <span>Declared config</span>
            <span className="rowhover-fold-total">{knobs ? knobs.length : "…"}</span>
          </summary>
          {knobs && (
            <dl className="rowhover-facts">
              {knobs.map((k) => (
                <Fragment key={k.path}>
                  <dt>{k.label}</dt>
                  <dd>{fmtValue(k.value)}</dd>
                </Fragment>
              ))}
            </dl>
          )}
          {knobs?.length === 0 && (
            <p className="rowhover-note">Every knob is at its default — nothing was declared.</p>
          )}
        </details>
      )}

      {campaignId && (
        <details className="rowhover-fold">
          <summary>
            <span>On disk</span>
            <span className="rowhover-fold-total">{size(data?.on_disk_bytes)}</span>
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
