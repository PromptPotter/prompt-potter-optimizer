import { useMemo } from "react";
import type { Evidence, SubjectReading } from "@/lib/api/types";
import { CardFrame, DataTable, Empty, ReadState, type Column } from "@/components/ui";
import { cx } from "@/lib/cx";
import { spinesOf } from "@/lib/derivations";
import { shownData } from "@/lib/hooks/useRead";
import { lineageIndexOf, useLineageTree } from "@/lib/lineage";
import { seriesVar } from "@/lib/theme";
import { ChannelRestore, ConfigCell, pointKeyOf } from "./config-edit";

function configured(evidence: Evidence): SubjectReading[] {
  return evidence.subjects.filter((s) => s.config !== null);
}

function ConfigPanels({ evidence }: { evidence: Evidence }) {
  const rows = configured(evidence);

  const differs = evidence.config_keys?.differs ?? [];
  const oneSided = evidence.config_keys?.one_sided ?? [];
  const same = evidence.config_keys?.same ?? [];

  const withoutConfig = evidence.subjects.filter((s) => s.config === null);

  if (rows.length === 0) {
    return (
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
            {r.label}
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

function AncestryPanels({ evidence }: { evidence: Evidence }) {
  // One group per campaign, named by the label its first subject's reading serves for it.
  const byCampaign = useMemo(() => {
    const out = new Map<string, { name: string; subjects: SubjectReading[] }>();
    for (const s of evidence.subjects) {
      const group = out.get(s.campaign_id);
      if (group) group.subjects.push(s);
      else out.set(s.campaign_id, { name: s.short_labels.campaign, subjects: [s] });
    }
    return [...out.entries()];
  }, [evidence.subjects]);

  if (byCampaign.length === 0) return null;
  return (
    <div className="cmp-spines">
      {byCampaign.map(([campaignId, { name, subjects }]) => (
        <CampaignSpines
          key={campaignId}
          campaignId={campaignId}
          name={name}
          subjects={subjects}
          evidence={evidence}
        />
      ))}
    </div>
  );
}

function CampaignSpines({
  campaignId,
  name,
  subjects,
  evidence,
}: {
  campaignId: string;
  // Its subjects' served `short_labels.campaign`.
  name: string;
  subjects: readonly SubjectReading[];
  evidence: Evidence;
}) {
  const read = useLineageTree(campaignId, true);
  const root = shownData(read);
  const index = lineageIndexOf(root);

  const spines = useMemo(() => spinesOf(index, subjects), [index, subjects]);

  if (root === null) {
    return (
      <ReadState read={read} subject={`${name}’s lineage`}>
        {() => null}
      </ReadState>
    );
  }
  if (spines.length === 0) {
    return (
      <p className="note-info">
        {name}: no ancestry to draw — its channels read at points the tree does not
        place, which is the case for an L4 inner run in its own sandbox.
      </p>
    );
  }

  return (
    <>
      {spines.map((spine) => (
        <div className="cmp-spine" key={spine.course.address}>
          <span className="cmp-spine-name" title={spine.course.address}>
            {spine.campaign.short_label} · {spine.course.short_label}
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

export function SearchpointCards({ evidence }: { evidence: Evidence }) {
  return (
    <>
      <CardFrame title="How these searchpoints are configured" headingTag="h2">
        <ConfigPanels evidence={evidence} />
      </CardFrame>
      <CardFrame title="How we got here" headingTag="h2">
        <p className="note-lede">
          The main line to each point. Two points on one chain share a spine — the shorter one
          is a prefix of the longer, so both are marked on the same line rather than drawn twice.
        </p>
        <AncestryPanels evidence={evidence} />
      </CardFrame>
    </>
  );
}
