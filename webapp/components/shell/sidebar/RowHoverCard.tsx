"use client";
import { Fragment, useState, type ReactNode } from "react";
import { CopyButton, HoverCard } from "@/components/ui";
import { readyData, useRead } from "@/lib/hooks/useRead";
import {
  campaignStorageRead,
  configMapRead,
  type ConfigKnob,
  type ConfigMapResponse,
  type HierarchyEntry,
} from "@/lib/api";
import type { SummaryFacts } from "@/lib/derivations";
import { SummaryBlock } from "@/components/shell/SummaryBlock";
import { fmtAgo, fmtBytes, fmtDateTime, fmtValue } from "@/lib/format";

export interface CandidateCard extends SummaryFacts {
  level: "candidate";
  lede: string;
  caveat?: ReactNode;
  facts: [string, string][];
}

const snake = (label: string) => label.toLowerCase().replace(/[^a-z0-9]+/g, "_");

// A knob moving several estimands is listed under each, so the path dedupes it.
function declaredKnobs(map: ConfigMapResponse): ConfigKnob[] {
  const byPath = new Map<string, ConfigKnob>();
  for (const group of map.groups) {
    for (const knob of group.knobs) {
      const scalar = knob.value == null || typeof knob.value !== "object";
      if (knob.source === "campaign" && scalar) byPath.set(knob.path, knob);
    }
  }
  return [...byPath.values()];
}

function settingValue(v: unknown): string {
  return Array.isArray(v) ? v.map((x) => fmtValue(x)).join(",") : fmtValue(v);
}

export function RowHoverCard({
  card,
  children,
}: {
  card: HierarchyEntry | CandidateCard;
  children: ReactNode;
}) {
  return (
    <HoverCard
      block
      className="rowhover-shell"
      content={card.level === "candidate" ? <CandidateBody card={card} /> : <EntryBody entry={card} />}
    >
      {children}
    </HoverCard>
  );
}

function Facts({ facts }: { facts: [string, string][] }) {
  return (
    <dl className="rowhover-facts">
      {facts.map(([label, value]) => (
        <Fragment key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

function CandidateBody({ card }: { card: CandidateCard }) {
  const payload = {
    name: card.label,
    ...(card.state ? { state: card.state.status.label } : {}),
    ...Object.fromEntries(card.stats.map((s) => [snake(s.label), s.value])),
    ...Object.fromEntries(card.facts.map(([k, v]) => [snake(k), v])),
  };
  return (
    <div className="rowhover">
      <SummaryBlock
        facts={card}
        lede={<p className="rowhover-lede">{card.lede}</p>}
        actions={<CopyButton title="Copy these details as JSON" data={payload} />}
      />
      {card.caveat && <div className="rowhover-caveat">{card.caveat}</div>}
      <Facts facts={card.facts} />
    </div>
  );
}

function EntryBody({ entry }: { entry: HierarchyEntry }) {
  const campaign = entry.level === "campaign" ? entry : null;
  const campaignId = campaign?.address ?? null;
  const read = useRead(campaignId != null ? campaignStorageRead(campaignId) : null);
  const data = readyData(read);
  const error = read.status === "failed";
  // Waits for the fold; latches true so closing it again keeps the answer.
  const [configAsked, setConfigAsked] = useState(false);
  const configMap = readyData(
    useRead(campaignId != null && configAsked ? configMapRead(campaignId) : null),
  );
  const knobs = configMap ? declaredKnobs(configMap) : null;
  const runsWith = campaign?.facts.runs_with ?? null;

  const sizes: [string, number | undefined, boolean?][] = [
    ["Dataset", data?.dataset_bytes],
    ["Connector", data?.connector_bytes],
    ["Loop", data?.loop_bytes],
    ["State", data?.state_bytes, true],
    ["Trace", data?.trace_bytes, true],
    ["History", data?.history_bytes, true],
    ["Reports", data?.reports_bytes, true],
  ];
  const size = (n: number | undefined) => (error ? "—" : n === undefined ? "…" : fmtBytes(n));
  const { opened_at, last_at } = entry.span;

  return (
    <div className="rowhover">
      <SummaryBlock
        facts={entry}
        lede={entry.lede ? <p className="rowhover-lede">{entry.lede}</p> : null}
        actions={<CopyButton title="Copy this entry as JSON" data={entry} />}
      />
      {entry.caveat && <div className="rowhover-caveat">{entry.caveat}</div>}
      <Facts
        facts={[
          ["Last activity", fmtAgo(last_at) || fmtDateTime(last_at)],
          ["Opened", fmtDateTime(opened_at)],
          ["Address", entry.address],
        ]}
      />

      {campaign && (
        <section>
          <p className="rowhover-heading">Runs with</p>
          {runsWith ? (
            <dl className="rowhover-facts">
              {runsWith.params.map((s) => (
                <Fragment key={`${s.node}.${s.key}`}>
                  <dt>
                    {s.node} · {s.key}
                  </dt>
                  <dd>
                    {settingValue(s.value)}
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
