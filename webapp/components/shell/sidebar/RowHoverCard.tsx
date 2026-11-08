import { Fragment, useState, type ReactNode } from "react";
import { CopyButton, HoverCard } from "@/components/ui";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { campaignStorageRead, configMapRead } from "@/lib/api";
import type {
  ConfigKnob,
  ConfigMapResponse,
  HierarchyEntry,
  UnstartedCampaign,
} from "@/lib/api/types";
import { LeafSplit } from "@/components/account/LeafSplit";
import { SummaryBlock } from "@/components/shell/SummaryBlock";
import { entryAlone } from "@/lib/derivations";
import { fmtAgo, fmtBytes, fmtDateTime, fmtValue, PLACEHOLDER } from "@/lib/format";

// What no layer set: a default, a required floor, or a manifest's own value.
const UNSET_SOURCES: ReadonlySet<ConfigKnob["source"]> = new Set(["default", "required", "manifest"]);

// A knob moving several estimands is listed under each, so the path dedupes it.
function declaredKnobs(map: ConfigMapResponse): ConfigKnob[] {
  const byPath = new Map<string, ConfigKnob>();
  for (const group of map.groups) {
    for (const knob of group.knobs) {
      const scalar = knob.value == null || typeof knob.value !== "object";
      if (!UNSET_SOURCES.has(knob.source) && scalar) byPath.set(knob.path, knob);
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
  card: HierarchyEntry | UnstartedCampaign;
  children: ReactNode;
}) {
  return (
    <HoverCard block className="rowhover-shell" content={<EntryBody entry={card} />}>
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

function EntryBody({ entry }: { entry: HierarchyEntry | UnstartedCampaign }) {
  const campaign = entry.level === "campaign" ? entry : null;
  // Storage and config are routed by a top-level campaign id; an inner run's sandbox has neither.
  const campaignId = campaign && campaign.parent === null ? campaign.address : null;
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

  // A round and a candidate have no run of their own, so no span.
  const span = entry.level === "candidate" ? null : entry.span;
  const reading = entry.level === "candidate" ? entry.facts.reading : null;
  const facts: [string, string][] = span
    ? [
        ["Last activity", fmtAgo(span.last_at) || fmtDateTime(span.last_at)],
        ["Opened", fmtDateTime(span.opened_at)],
      ]
    : [];
  if (reading) facts.push(["Round", String(reading.arm.round)]);
  if (reading?.sp_hash) facts.push(["Searchpoint", reading.sp_hash]);
  facts.push(["Address", entry.address]);

  return (
    <div className="rowhover">
      <SummaryBlock
        entry={entry}
        // The served entry alone, one shape whatever the card has opened: a program reads it.
        actions={<CopyButton title="Copy this entry as JSON" data={entryAlone(entry)} />}
      />
      <Facts facts={facts} />

      {campaign && runsWith && (
        <section>
          <p className="rowhover-heading">Runs with</p>
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
            <span className="rowhover-fold-total">
              {data ? fmtBytes(data.total) : error ? PLACEHOLDER : "…"}
            </span>
          </summary>
          {data && <LeafSplit bytes={data} />}
        </details>
      )}
    </div>
  );
}
