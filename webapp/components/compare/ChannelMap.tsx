"use client";

import { useCallback, useMemo, useState } from "react";
import type { ArmNode, SubjectReading } from "@/lib/api";
import { candidateSubject, readingPath } from "@/lib/api/reads";
import {
  Forest,
  type CladogramChannel,
  type CladogramCtx,
} from "@/components/candidates/Forest";
import { DENSE, type RoundNodePos } from "@/components/candidates/forest-layout";
import { useCompareSelection } from "@/lib/compare-selection";
import { nodeKeyOf, nodeOverlays, pathOf, walkCourses } from "@/lib/derivations";
import { fmtPct0 } from "@/lib/format";
import { encodeCyclePath } from "@/lib/ids";
import type { ChannelModel } from "./useChannelModel";

export function ChannelMap({
  model,
  reading,
  points,
  own,
}: {
  model: ChannelModel;
  reading: SubjectReading | null;
  points: readonly CladogramChannel[];
  own: CladogramChannel | null;
}) {
  const { tree, selected, setSelected, invalidated, subject } = model;
  const { root, index, loaded, failed, rootPath } = tree;
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const [whole, setWhole] = useState(false);

  const courses = useMemo(() => (root ? walkCourses(root) : []), [root]);
  const { valueByKey, thetaByKey } = useMemo(() => nodeOverlays(courses, "accuracy"), [courses]);

  const viewedKey = useMemo(
    () => (reading ? encodeCyclePath(readingPath(reading)) : null),
    [reading],
  );
  const laneKeys = useMemo(() => {
    const keys = [
      ...new Set(
        points.flatMap((p) => {
          const course = index.get(p.coursePathKey)?.course;
          return course ? [nodeKeyOf(course)] : [];
        }),
      ),
    ];
    if (keys.length > 0) return keys;
    return root ? [nodeKeyOf(root)] : [];
  }, [root, points, index]);
  // Compared as a string: `points` is rebuilt every evidence fetch, and re-seeding would drop opened lanes.
  const seed = laneKeys.join("~");
  const [seeded, setSeeded] = useState<string | null>(null);
  if (seed && seed !== seeded) {
    setSeeded(seed);
    // `expanded` only: clearing the selection here leaves the head picker lit on nothing.
    setExpanded(new Set(laneKeys));
  }

  const onLaneActivate = useCallback((key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  }, []);

  const ctx = useMemo<CladogramCtx>(
    () => ({
      viewedKey,
      channels: points,
      clip: whole ? null : own,
      invalidated,
      isPicked: (n: RoundNodePos) =>
        !!selected &&
        n.candidateId === selected.id &&
        n.coursePathKey === encodeCyclePath(pathOf(selected)),
      // The served node, not a lane lookup: a fork-contributed attempt is indexed under the fork's lane.
      onPickCandidate: (n: RoundNodePos) => setSelected(n.node === selected ? null : n.node),
    }),
    [viewedKey, selected, setSelected, points, own, whole, invalidated],
  );

  if (rootPath.length === 0) {
    return <p className="note-empty">This campaign has no branch in the registry to map.</p>;
  }
  if (failed) return <p className="note-warn">This campaign&rsquo;s lineage could not be read.</p>;
  if (!root) {
    return <p className="note-empty">{loaded ? "No rounds on disk yet." : "Reading the lineage…"}</p>;
  }

  return (
    <div className="cmp-channel-map">
      {own && (
        <p className="cmp-map-scope">
          <span className="l4-dim">
            {whole
              ? "The whole campaign — everything after this point too."
              : "How this point came to be — the family up to it, and no further."}
          </span>
          <button type="button" className="cmp-link" onClick={() => setWhole((v) => !v)}>
            {whole ? "Cut to this point" : "Show the whole campaign"}
          </button>
        </p>
      )}
      {selected && <ArmedActions armed={selected} subject={subject} />}
      <Forest
        tree={root}
        valueByKey={valueByKey}
        thetaByKey={thetaByKey}
        metric="accuracy"
        expanded={expanded}
        onLaneActivate={onLaneActivate}
        ctx={ctx}
        d={DENSE}
      />
    </div>
  );
}

function ArmedActions({ armed, subject }: { armed: ArmNode; subject: string }) {
  const { hasSubject, addSubject, replace } = useCompareSelection();
  const accuracy = armed.reading.own?.accuracy?.value ?? null;
  const path = pathOf(armed);
  const leaf = path.at(-1);
  const top = path.at(0);
  if (!leaf || !top) return null;
  const next = candidateSubject(path, armed.id);
  if (next === subject) return null;
  const already = hasSubject(next);
  return (
    <p className="cmp-armed">
      <strong>{armed.label}</strong>
      <span className="l4-dim">
        {" "}
        R{armed.reading.arm.round} · {accuracy === null ? "—" : fmtPct0(accuracy)}
      </span>
      {accuracy === null && (
        <span className="l4-dim">nothing scored here yet — it reads as unmeasured</span>
      )}
      <button
        type="button"
        className="cmp-button"
        disabled={next === subject}
        onClick={() => replace(subject, next)}
      >
        Move this channel here
      </button>
      <button
        type="button"
        className="cmp-button"
        disabled={already}
        onClick={() => addSubject({ rootCampaignId: top.campaignId, subject: next })}
      >
        {already ? "Already a channel" : "Compare — add as a channel"}
      </button>
    </p>
  );
}
