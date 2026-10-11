"use client";

import { useMemo, useState } from "react";
import type { ArmNode, Evidence, SubjectReading } from "@/lib/api";
import { CardFrame } from "@/components/ui";
import { cx } from "@/lib/cx";
import { indexLineage, pathOf } from "@/lib/derivations";
import { shortId } from "@/lib/format";
import type { CyclePath } from "@/lib/ids";
import { useLineageTree } from "@/lib/lineage";
import { seriesVar } from "@/lib/theme";
import { ChannelRestore, ConfigCell, pointKeyOf } from "./config-edit";

function configured(evidence: Evidence): SubjectReading[] {
  return evidence.subjects.filter((s) => s.config !== null);
}

function ConfigPanels({ evidence, loading }: { evidence: Evidence; loading: boolean }) {
  const rows = configured(evidence);
  const [showSame, setShowSame] = useState(false);

  const differs = evidence.config_keys?.differs ?? [];
  const oneSided = evidence.config_keys?.one_sided ?? [];
  const same = evidence.config_keys?.same ?? [];

  const withoutConfig = evidence.subjects.filter((s) => s.config === null);

  if (rows.length === 0) {
    return (
      <p className={loading ? "note-lede" : "note-empty"}>
        {loading
          ? "Reading the searchpoints…"
          : "None of these searchpoints recorded a configuration. A round file written before `resolved_pipeline_params` carries none, and nothing here reconstructs one."}
      </p>
    );
  }

  return (
    <div className="cmp-cfg-wrap">
      {rows.length === 1 && (
        <p className="note-info">
          One searchpoint, so there is nothing to line it up against — this is what it IS. Add a
          second channel to see which keys differ.
        </p>
      )}
      {withoutConfig.length > 0 && (
        <p className="note-info">
          Not in this table: {withoutConfig.map((s) => s.label).join(", ")} — read, but the round
          file at that point records no configuration, so there is nothing to line up.
        </p>
      )}
      <p className="l4-subtle">
        Edit any value to ask what it would have taken. Nothing ran under an edited one, so no
        measurement on that channel carries over — the numbers become <code>?</code> rather than
        being recomputed, and <code>↺</code> puts it back on the record. The two settings that CAN
        be re-read from what was measured are the scoring criterion and the sample subset, and both
        ride the scoring mask beside the chart above.
      </p>
      <table className="cmp-cfg">
        <thead>
          <tr>
            <th scope="col">key</th>
            {rows.map((r) => (
              <th scope="col" key={r.key} title={r.key}>
                <span
                  className="cmp-swatch"
                  style={{ background: seriesVar(evidence.subjects.indexOf(r)) }}
                  aria-hidden="true"
                />
                {r.kind === "campaign" ? shortId(r.label) : r.label}
                <ChannelRestore subjectKey={pointKeyOf(r)} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <Band n={differs.length} colSpan={rows.length + 1}>
            {differs.length} key{differs.length === 1 ? "" : "s"} set differently
          </Band>
          {differs.map((key) => (
            <Row key={key} name={key} rows={rows} differing />
          ))}
          <Band n={oneSided.length} colSpan={rows.length + 1}>
            {oneSided.length} only one of these configures — not a disagreement, a different
            pipeline
          </Band>
          {oneSided.map((key) => (
            <Row key={key} name={key} rows={rows} />
          ))}
          <tr className="cmp-cfg-band">
            <th scope="row" colSpan={rows.length + 1}>
              <button
                type="button"
                className="cmp-link"
                aria-expanded={showSame}
                onClick={() => setShowSame((v) => !v)}
                disabled={same.length === 0}
              >
                {showSame ? "▾" : "▸"} {same.length} identical
              </button>
            </th>
          </tr>
          {showSame &&
            same.map((key) => <Row key={key} name={key} rows={rows} />)}
        </tbody>
      </table>
    </div>
  );
}

function Band({
  n,
  colSpan,
  children,
}: {
  n: number;
  colSpan: number;
  children: React.ReactNode;
}) {
  if (n === 0) return null;
  return (
    <tr className="cmp-cfg-band">
      <th scope="row" colSpan={colSpan}>
        {children}
      </th>
    </tr>
  );
}

function Row({
  name,
  rows,
  differing,
}: {
  name: string;
  rows: readonly SubjectReading[];
  differing?: boolean;
}) {
  return (
    <tr className={cx("l4-row", differing && "cmp-cfg-differs")}>
      <th scope="row" className="cmp-cfg-key" title={name}>
        {name}
      </th>
      {rows.map((r) => (
        <td key={r.key} className="cmp-cfg-val">
          <ConfigCell
            name={name}
            subjectKey={pointKeyOf(r)}
            label={r.label}
            served={r.config?.[name]}
          />
        </td>
      ))}
    </tr>
  );
}

interface Spine {
  campaignId: string;
  cycleId: string;
  chain: ArmNode[];
  marked: Map<string, string[]>;
}

function AncestryPanels({ evidence }: { evidence: Evidence }) {
  const byCampaign = useMemo(() => {
    const out = new Map<string, SubjectReading[]>();
    for (const s of evidence.subjects) {
      out.set(s.campaign_id, [...(out.get(s.campaign_id) ?? []), s]);
    }
    return [...out.entries()];
  }, [evidence.subjects]);

  if (byCampaign.length === 0) return null;
  return (
    <div className="cmp-spines">
      {byCampaign.map(([campaignId, subjects]) => (
        <CampaignSpines
          key={campaignId}
          campaignId={campaignId}
          subjects={subjects}
          evidence={evidence}
        />
      ))}
    </div>
  );
}

function CampaignSpines({
  campaignId,
  subjects,
  evidence,
}: {
  campaignId: string;
  subjects: readonly SubjectReading[];
  evidence: Evidence;
}) {
  const rootCycleId = subjects[0]?.cycle_id ?? "";
  const path = useMemo<CyclePath>(
    () => [{ campaignId, cycleId: rootCycleId }],
    [campaignId, rootCycleId],
  );
  const { root, loaded, failed } = useLineageTree(path, rootCycleId !== "");
  const index = useMemo(() => indexLineage(root), [root]);

  const spines = useMemo(() => buildSpines(index, subjects), [index, subjects]);

  if (failed) {
    return <p className="note-warn">Could not read {shortId(campaignId)}&rsquo;s lineage.</p>;
  }
  if (!loaded) return <p className="note-empty">Reading {shortId(campaignId)}&rsquo;s lineage…</p>;
  if (spines.length === 0) {
    return (
      <p className="note-info">
        {shortId(campaignId)}: no ancestry to draw — its channels read at points the tree does not
        place, which is the case for an L4 inner run in its own sandbox.
      </p>
    );
  }

  return (
    <>
      {spines.map((spine) => (
        <div className="cmp-spine" key={`${spine.campaignId}|${spine.cycleId}`}>
          <span className="cmp-spine-name" title={`${spine.campaignId} / ${spine.cycleId}`}>
            {shortId(spine.campaignId)} · {shortId(spine.cycleId)}
          </span>
          <ol className="cmp-spine-chain">
            {spine.chain.map((node) => {
              const marks = spine.marked.get(node.id) ?? [];
              return (
                <li
                  key={node.id}
                  className={cx("cmp-spine-node", marks.length > 0 && "is-marked")}
                  title={node.label}
                >
                  <span className="cmp-spine-label">{node.label}</span>
                  {marks.map((label) => (
                    <span
                      className="cmp-spine-mark"
                      key={label}
                      style={{
                        background: seriesVar(
                          evidence.subjects.findIndex((s) => s.label === label),
                        ),
                      }}
                    >
                      {label}
                    </span>
                  ))}
                </li>
              );
            })}
          </ol>
        </div>
      ))}
    </>
  );
}

function buildSpines(
  index: ReturnType<typeof indexLineage>,
  subjects: readonly SubjectReading[],
): Spine[] {
  const nodes = new Map<string, ArmNode>();
  const courseOf = new Map<string, string>();
  for (const [addr, entry] of index) {
    for (const cand of entry.candidates) {
      nodes.set(cand.id, cand);
      courseOf.set(cand.id, addr);
    }
  }

  const chains = new Map<string, { chain: ArmNode[]; marked: Map<string, string[]> }>();
  for (const s of subjects) {
    const head = nodes.get(s.candidate_id);
    if (!head) continue;
    const chain: ArmNode[] = [];
    const seen = new Set<string>();
    let cursor: ArmNode | undefined = head;
    while (cursor && !seen.has(cursor.id)) {
      seen.add(cursor.id);
      chain.unshift(cursor);
      cursor = cursor.parent_ids[0] ? nodes.get(cursor.parent_ids[0]) : undefined;
    }
    const rootId = chain[0]?.id ?? s.candidate_id;
    const prior = chains.get(rootId);
    const merged = !prior || chain.length > prior.chain.length ? chain : prior.chain;
    const marked = prior?.marked ?? new Map<string, string[]>();
    marked.set(s.candidate_id, [...(marked.get(s.candidate_id) ?? []), s.label]);
    chains.set(rootId, { chain: merged, marked });
  }

  return [...chains.values()].map(({ chain, marked }) => {
    const head = chain.at(-1);
    const addr = head ? (courseOf.get(head.id) ?? "") : "";
    const hops: CyclePath = head ? pathOf(head) : [];
    const leaf = hops.at(-1);
    return {
      campaignId: leaf?.campaignId ?? "",
      cycleId: leaf?.cycleId ?? addr,
      chain,
      marked,
    };
  });
}

export function SearchpointCards({
  evidence,
  loading,
}: {
  evidence: Evidence;
  loading: boolean;
}) {
  return (
    <>
      <CardFrame title="How these searchpoints are configured" headingTag="h2">
        <ConfigPanels evidence={evidence} loading={loading} />
      </CardFrame>
      <CardFrame title="How we got here" headingTag="h2">
        <p className="note-lede">
          The parent chain to each point. Two points on one chain share a spine — the shorter one
          is a prefix of the longer, so both are marked on the same line rather than drawn twice.
        </p>
        <AncestryPanels evidence={evidence} />
      </CardFrame>
    </>
  );
}
