"use client";
// What the selected searchpoints ARE, side by side. Configuration is SERVED resolved (`SubjectReading.config`);
// ancestry walks `parent_id` on the one served tree (`webapp/CLAUDE.md`), never a second read.

import { useMemo, useState } from "react";
import type { Evidence, LineageNode, SubjectReading } from "@/lib/api";
import { CardFrame } from "@/components/ui";
import { cx } from "@/lib/cx";
import { indexLineage, pathOf } from "@/lib/derivations";
import { shortId } from "@/lib/format";
import type { CyclePath } from "@/lib/ids";
import { useLineageTree } from "@/lib/lineage";
import { seriesVar } from "@/lib/theme";
import { ChannelRestore, ConfigCell, pointKeyOf, type ScenarioEdits } from "./config-edit";

function configured(evidence: Evidence): SubjectReading[] {
  return evidence.subjects.filter((s) => s.config !== null);
}

export function ConfigPanels({
  evidence,
  loading,
  edits,
  onEdits,
}: {
  evidence: Evidence;
  loading: boolean;
  edits: ScenarioEdits;
  onEdits: (next: ScenarioEdits) => void;
}) {
  const rows = configured(evidence);
  const [showSame, setShowSame] = useState(false);

  // THREE bands: a key only one side carries is a different finding from a key set differently —
  // two pipelines sharing no key would otherwise read as "17 keys differ".
  const { differs, oneSided, same } = useMemo(() => {
    const keys = [...new Set(rows.flatMap((r) => Object.keys(r.config ?? {})))].sort();
    const split: { differs: string[]; oneSided: string[]; same: string[] } = {
      differs: [],
      oneSided: [],
      same: [],
    };
    for (const key of keys) {
      const present = rows.filter((r) => r.config?.[key] !== undefined);
      if (present.length < rows.length) split.oneSided.push(key);
      else if (new Set(present.map((r) => r.config?.[key])).size > 1) split.differs.push(key);
      else split.same.push(key);
    }
    return split;
  }, [rows]);

  // Named rather than silently absent, so "records no config" is not read as "the panel dropped it".
  const withoutConfig = evidence.subjects.filter((s) => s.config === null);

  if (rows.length === 0) {
    return (
      <p className={loading ? "l4-lede" : "l4-empty"}>
        {loading
          ? "Reading the searchpoints…"
          : "None of these searchpoints recorded a configuration. A round document written before `resolved_pipeline_params` carries none, and nothing here reconstructs one."}
      </p>
    );
  }

  return (
    <div className="cmp-cfg-wrap">
      {rows.length === 1 && (
        <p className="l4-note">
          One searchpoint, so there is nothing to line it up against — this is what it IS. Add a
          second channel to see which keys differ.
        </p>
      )}
      {withoutConfig.length > 0 && (
        <p className="l4-note">
          Not in this table: {withoutConfig.map((s) => s.label).join(", ")} — read, but the round
          document at that point records no configuration, so there is nothing to line up.
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
                {/* Keyed on the resolved POINT (`pointKeyOf`), the same key the card's editor writes. */}
                <ChannelRestore edits={edits} subjectKey={pointKeyOf(r)} onEdits={onEdits} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <Band n={differs.length} colSpan={rows.length + 1}>
            {differs.length} key{differs.length === 1 ? "" : "s"} set differently
          </Band>
          {differs.map((key) => (
            <Row key={key} name={key} rows={rows} edits={edits} onEdits={onEdits} differing />
          ))}
          <Band n={oneSided.length} colSpan={rows.length + 1}>
            {oneSided.length} only one of these configures — not a disagreement, a different
            pipeline
          </Band>
          {oneSided.map((key) => (
            <Row key={key} name={key} rows={rows} edits={edits} onEdits={onEdits} />
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
            same.map((key) => (
              <Row key={key} name={key} rows={rows} edits={edits} onEdits={onEdits} />
            ))}
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

// The CELL is shared with the channel card's editor (`config-edit.tsx`), so one value has one editor.
function Row({
  name,
  rows,
  edits,
  onEdits,
  differing,
}: {
  name: string;
  rows: readonly SubjectReading[];
  edits: ScenarioEdits;
  onEdits: (next: ScenarioEdits) => void;
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
            edits={edits}
            onEdits={onEdits}
          />
        </td>
      ))}
    </tr>
  );
}

// One spine per CAMPAIGN, not per subject: a shared prefix shows one point extends the other.

interface Spine {
  campaignId: string;
  cycleId: string;
  chain: LineageNode[];
  marked: Map<string, string[]>; // candidate id -> the channel labels sitting on it
}

export function AncestryPanels({ evidence }: { evidence: Evidence }) {
  // Grouped first so a campaign contributing two channels fetches its tree once.
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
    return <p className="l4-warn">Could not read {shortId(campaignId)}&rsquo;s lineage.</p>;
  }
  if (!loaded) return <p className="l4-empty">Reading {shortId(campaignId)}&rsquo;s lineage…</p>;
  if (spines.length === 0) {
    return (
      <p className="l4-note">
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
  const nodes = new Map<string, LineageNode>();
  const courseOf = new Map<string, string>();
  for (const [addr, entry] of index) {
    for (const cand of entry.candidates) {
      nodes.set(cand.id, cand);
      courseOf.set(cand.id, addr);
    }
  }

  const chains = new Map<string, { chain: LineageNode[]; marked: Map<string, string[]> }>();
  for (const s of subjects) {
    const head = nodes.get(s.candidate_id);
    if (!head) continue;
    const chain: LineageNode[] = [];
    const seen = new Set<string>();
    let cursor: LineageNode | undefined = head;
    while (cursor && !seen.has(cursor.id)) {
      seen.add(cursor.id);
      chain.unshift(cursor);
      cursor = cursor.parent_id ? nodes.get(cursor.parent_id) : undefined;
    }
    // Keyed on the chain's ROOT, so the longer chain of a shared origin wins.
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
  edits,
  onEdits,
}: {
  evidence: Evidence;
  loading: boolean;
  edits: ScenarioEdits;
  onEdits: (next: ScenarioEdits) => void;
}) {
  // Rendered even when empty — a vanished card is indistinguishable from one that dropped channels.
  return (
    <>
      <CardFrame title="How these searchpoints are configured" headingTag="h2">
        <ConfigPanels evidence={evidence} loading={loading} edits={edits} onEdits={onEdits} />
      </CardFrame>
      <CardFrame title="How we got here" headingTag="h2">
        <p className="l4-lede">
          The parent chain to each point. Two points on one chain share a spine — the shorter one
          is a prefix of the longer, so both are marked on the same line rather than drawn twice.
        </p>
        <AncestryPanels evidence={evidence} />
      </CardFrame>
    </>
  );
}
