"use client";
// WHAT the selected searchpoints ARE, side by side — as against what they scored, which is every
// other card on this tab.
//
// Two questions, two panels, and they take their data from different places on purpose:
//
//   Configuration — SERVED per subject (`SubjectReading.config`), one flat `key -> value` map over
//   the RESOLVED node config plus the prompt fields. Resolved rather than each candidate's sparse
//   delta: two searchpoints from different campaigns share no delta, so lined up on deltas the
//   panels would have nothing in common to line up ON. Nothing here re-merges a config.
//
//   Ancestry — derived from THE served tree, by walking `parent_id`. It is not fetched again: the
//   picker already subscribed each campaign's tree, and `webapp/CLAUDE.md` is explicit that a leaf
//   surface addresses into that one genealogy rather than reading its own.
//
// The alignment is a set union over served keys and a string comparison of served values. It
// introduces no number, which is the line `lib/derivations/*` draws.

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

// The channels this card is about. A campaign or an un-drilled course has a configuration too —
// its head's — so all three kinds are welcome; what the card needs is two of them.
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

  // THREE bands, not two. A key only one side carries and a key both set differently are
  // different findings, and lumping them is what makes two subjects from different pipelines read
  // as "17 keys differ" — every one of them being "the other one has no such param". A pp-self
  // outer origin and an inner justlogic searchpoint share no key at all, which is a fact about the
  // pair rather than seventeen disagreements.
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

  // Selected, read, and carrying no configuration — named rather than silently absent from the
  // table. A channel that simply vanishes here reads as a bug in the card, which is what it was:
  // an operator looking for `C2.2` had no way to tell "its round document records none" from
  // "this panel dropped it".
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
                {/* Keyed on the POINT this channel reads at, not on the channel's own address:
                    a `campaign:` or `course:` channel names a branch while the edit lands on the
                    searchpoint the server resolved it to. Same key the card's editor writes, which
                    is what keeps the two synchronous. */}
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

// A band heading, silent when its group is empty — an empty "0 keys set differently" row is a
// finding announced for having found nothing.
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

// A prose field is a paragraph; the cell holds it in a single line and carries the whole thing in
// `title`, so the table stays a table and nothing is lost. The CELL itself is shared with the
// channel card's own expandable (`config-edit.tsx`), which is what keeps the two editors of one
// value from drifting apart.
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

// ── Ancestry ────────────────────────────────────────────────────────────────
// How we got to each point. One spine per CAMPAIGN, not per subject: when one selected point
// descends from another they share a chain, and drawing it twice would hide the very thing the
// shared prefix says — that one is an extension of the other.

interface Spine {
  campaignId: string;
  cycleId: string;
  chain: LineageNode[];
  marked: Map<string, string[]>; // candidate id -> the channel labels sitting on it
}

export function AncestryPanels({ evidence }: { evidence: Evidence }) {
  // One row per campaign in the selection, each opening its own tree subscription. Grouped
  // first so a campaign contributing two channels still fetches once.
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
  // Rooted at the campaign's own root cycle. Every selected channel of this campaign carries the
  // same one, so any of them names it.
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

// The parent chain to each selected point, merged where one contains another. Pure walk over the
// served tree: `parent_id` is the genealogy the server already decided, and following it is not
// deriving a second one.
function buildSpines(
  index: ReturnType<typeof indexLineage>,
  subjects: readonly SubjectReading[],
): Spine[] {
  // Every candidate in this campaign's tree, by id — the walk's lookup table.
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
    // Bounded by `seen`: a tree that somehow cycles must not hang the tab.
    while (cursor && !seen.has(cursor.id)) {
      seen.add(cursor.id);
      chain.unshift(cursor);
      cursor = cursor.parent_id ? nodes.get(cursor.parent_id) : undefined;
    }
    // Key on the ROOT of the chain: two points that share an origin share a spine, and the
    // longer chain wins — which is precisely "one tree is a subset of the other".
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
  // Rendered even with nothing to show. A card that disappears when its channels record no
  // configuration is indistinguishable from one that dropped them, which is the read that sent
  // an operator looking for a searchpoint that was never in it.
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
