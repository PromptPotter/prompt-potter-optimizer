"use client";

import { useMemo } from "react";
import type { Evidence, HierarchyCandidate, SubjectReading } from "@/lib/api";
import { CardFrame, DataTable, Empty, Loading, type Column } from "@/components/ui";
import { cx } from "@/lib/cx";
import { indexLineage } from "@/lib/derivations";
import { pathOfAddress } from "@/lib/ids";
import { shortId } from "@/lib/format";
import { useLineageTree } from "@/lib/lineage";
import { seriesVar } from "@/lib/theme";
import { ChannelRestore, ConfigCell, pointKeyOf } from "./config-edit";

function configured(evidence: Evidence): SubjectReading[] {
  return evidence.subjects.filter((s) => s.config !== null);
}

function ConfigPanels({ evidence, loading }: { evidence: Evidence; loading: boolean }) {
  const rows = configured(evidence);

  const differs = evidence.config_keys?.differs ?? [];
  const oneSided = evidence.config_keys?.one_sided ?? [];
  const same = evidence.config_keys?.same ?? [];

  const withoutConfig = evidence.subjects.filter((s) => s.config === null);

  if (rows.length === 0) {
    return loading ? (
      <Loading>Reading the searchpoints…</Loading>
    ) : (
      <Empty>
        None of these searchpoints recorded a configuration. A proposed arm resolves none, and
        nothing here reconstructs one.
      </Empty>
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
      <DataTable
        ariaLabel="Configuration by searchpoint"
        className="cmp-cfg"
        columns={configColumns(rows, evidence.subjects)}
        groups={[
          {
            key: "differs",
            open: true,
            header: `${differs.length} key${differs.length === 1 ? "" : "s"} set differently`,
            rows: differs.map((name) => ({ name, differing: true })),
          },
          {
            key: "one-sided",
            open: true,
            header: `${oneSided.length} only one of these configures — not a disagreement, a different pipeline`,
            rows: oneSided.map((name) => ({ name, differing: false })),
          },
          {
            key: "same",
            header: `${same.length} identical`,
            rows: same.map((name) => ({ name, differing: false })),
          },
        ].filter((g) => g.rows.length > 0)}
        getRowId={(k) => k.name}
      />
    </div>
  );
}

interface ConfigKey {
  name: string;
  differing: boolean;
}

function configColumns(
  rows: readonly SubjectReading[],
  subjects: readonly SubjectReading[],
): Column<ConfigKey>[] {
  return [
    {
      id: "key",
      label: "key",
      width: "minmax(14ch, 1fr)",
      cell: (k) => (
        <span className={cx("cmp-cfg-key", k.differing && "cmp-cfg-differs")} title={k.name}>
          {k.name}
        </span>
      ),
    },
    ...rows.map(
      (r): Column<ConfigKey> => ({
        id: r.key,
        label: (
          <span className="cmp-cfg-head" title={r.key}>
            <span
              className="cmp-swatch"
              style={{ background: seriesVar(subjects.indexOf(r)) }}
              aria-hidden="true"
            />
            {r.kind === "campaign" ? shortId(r.label) : r.label}
            <ChannelRestore subjectKey={pointKeyOf(r)} />
          </span>
        ),
        width: "minmax(16ch, 22rem)",
        wrap: true,
        cell: (k) => (
          <span className={cx("cmp-cfg-val", k.differing && "cmp-cfg-differs")}>
            <ConfigCell
              name={k.name}
              subjectKey={pointKeyOf(r)}
              label={r.label}
              served={r.config?.[k.name]}
            />
          </span>
        ),
      }),
    ),
  ];
}

interface Spine {
  campaignId: string;
  cycleId: string;
  chain: HierarchyCandidate[];
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
  const { root, loaded, failed } = useLineageTree(campaignId, true);
  const index = useMemo(() => indexLineage(root), [root]);

  const spines = useMemo(() => buildSpines(index, subjects), [index, subjects]);

  if (failed) {
    return <p className="note-warn">Could not read {shortId(campaignId)}&rsquo;s lineage.</p>;
  }
  if (!loaded) return <Loading>Reading {shortId(campaignId)}&rsquo;s lineage…</Loading>;
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
              const marks = spine.marked.get(node.facts.individual) ?? [];
              return (
                <li
                  key={node.address}
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
  const nodes = new Map<string, HierarchyCandidate>();
  for (const cand of index.candidates) {
    if (cand.facts.answers_for_id) nodes.set(cand.facts.individual, cand);
  }

  const chains = new Map<string, { chain: HierarchyCandidate[]; marked: Map<string, string[]> }>();
  for (const s of subjects) {
    const head = nodes.get(s.candidate_id);
    if (!head) continue;
    const chain: HierarchyCandidate[] = [];
    const seen = new Set<string>();
    let cursor: HierarchyCandidate | undefined = head;
    while (cursor && !seen.has(cursor.facts.individual)) {
      seen.add(cursor.facts.individual);
      chain.unshift(cursor);
      const parent: string | undefined = cursor.facts.parent_ids[0];
      cursor = parent ? nodes.get(parent) : undefined;
    }
    const rootId = chain[0]?.facts.individual ?? s.candidate_id;
    const prior = chains.get(rootId);
    const merged = !prior || chain.length > prior.chain.length ? chain : prior.chain;
    const marked = prior?.marked ?? new Map<string, string[]>();
    marked.set(s.candidate_id, [...(marked.get(s.candidate_id) ?? []), s.label]);
    chains.set(rootId, { chain: merged, marked });
  }

  return [...chains.values()].map(({ chain, marked }) => {
    const head = chain.at(-1);
    const leaf = head ? pathOfAddress(head.address).at(-1) : undefined;
    return {
      campaignId: leaf?.campaignId ?? "",
      cycleId: leaf?.cycleId ?? "",
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
