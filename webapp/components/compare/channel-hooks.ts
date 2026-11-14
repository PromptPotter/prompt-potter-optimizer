import { useMemo } from "react";
import type { CladogramChannel } from "@/components/candidates/Forest";
import type { HierarchyCampaign, HierarchyCandidate, SubjectReading } from "@/lib/api/types";
import { candidateSubject, readingPath } from "@/lib/api/reads";
import {
  applyFlatEdits,
  type channelArms,
  descendantsOf,
  mainLineOf,
  type CompareItem,
  type LineageIndex,
} from "@/lib/derivations";
import type { CellsPreset } from "@/lib/hooks/useCells";
import { shownData } from "@/lib/hooks/useRead";
import { useRound } from "@/lib/hooks/useRound";
import { lineageIndexOf, useLineageTree } from "@/lib/lineage";
import { fmtMetric, fmtMetricInterval, type MetricFormat } from "@/lib/format";
import { type CyclePath, decodeCyclePath, encodeCyclePath, pathOfAddress } from "@/lib/ids";
import { editsFor, pointKeyOf, useScenarioEdits } from "./config-edit";

// The channel's own plotted series; null for an unread channel, whose ink would be another's.
export function channelInk({ slot }: CompareItem, points: readonly CladogramChannel[]) {
  return slot === null ? null : (points[slot] ?? null);
}

// A per-channel ruler note shows only where the roster carries none of its own.
export function rulerNoteOf({ reading }: CompareItem, ownNote: boolean): boolean {
  return ownNote && reading?.comparable === false;
}

// A channel's campaign tree: every part of the card that asks shares the one read and its index.
export function useChannelTree(run: HierarchyCampaign | null) {
  const rootAddress = run?.facts.root ?? null;
  const rootPath = useMemo<CyclePath>(
    () => (rootAddress ? (decodeCyclePath(rootAddress) ?? []) : []),
    [rootAddress],
  );
  const read = useLineageTree(run?.address ?? "", rootPath.length > 0);
  return { read, index: lineageIndexOf(shownData(read)), rootPath };
}

export function useChannelHead(
  { own, winner, latest }: ReturnType<typeof channelArms>,
  selected: HierarchyCandidate | null,
) {
  return useMemo(() => {
    const sameAs = (a: HierarchyCandidate | null, b: HierarchyCandidate | null) =>
      !!a && !!b && a.address === b.address;
    const isWinner = sameAs(selected, winner);
    const isLatest = sameAs(selected, latest);
    const byValue: Record<string, HierarchyCandidate | null> = {
      winner,
      latest,
      picked: isWinner || isLatest ? null : selected,
    };
    const options = [
      winner && {
        value: "winner",
        label: "Winner",
        title: "The last searchpoint an election on this branch crowned",
      },
      latest &&
        !sameAs(latest, winner) && {
          value: "latest",
          label: "Most recent",
          title: "The newest searchpoint this branch minted, crowned or not",
        },
      selected &&
        !isWinner &&
        !isLatest && {
          value: "picked",
          label: "Picked",
          title: "The searchpoint clicked on the map below",
        },
    ].filter(Boolean) as { value: string; label: string; title: string }[];
    return {
      options,
      own,
      value: isWinner ? "winner" : isLatest ? "latest" : "picked",
      nodeFor: (v: string) => byValue[v] ?? null,
    };
  }, [own, winner, latest, selected]);
}

// The individuals an unrun scenario edit withdrew: every edited seed and its descendants.
export function useInvalidated(index: LineageIndex): ReadonlySet<string> {
  const { edits } = useScenarioEdits();
  return useMemo(() => {
    const seeds: string[] = [];
    for (const c of index.candidates) {
      const id = c.facts.individual;
      if (editsFor(edits, candidateSubject(pathOfAddress(c.address), id)).size > 0) seeds.push(id);
    }
    return descendantsOf(index, seeds);
  }, [index, edits]);
}

// The channel's headline number, or `?` once an edit above it withdrew every measurement.
export function channelLevel(
  reading: SubjectReading | null,
  withdrawn: boolean,
  metric: MetricFormat,
) {
  return (
    reading && {
      value: withdrawn ? "?" : fmtMetric(metric, reading.value),
      band: withdrawn
        ? "? · ? cells"
        : `${fmtMetricInterval(metric, reading.ci_lo, reading.ci_hi)} · ${reading.n_cells} cell${
            reading.n_cells === 1 ? "" : "s"
          }`,
    }
  );
}

// The highlighted searchpoint's own reads: `ChannelDetail` mounts only while its fold is open.
export function usePickedPoint(
  { channel, reading }: CompareItem,
  selected: HierarchyCandidate | null,
  index: LineageIndex,
) {
  const { edits } = useScenarioEdits();
  const subject = channel.subject;
  const path = useMemo(() => (selected ? pathOfAddress(selected.address) : null), [selected]);
  const round = selected?.facts.reading.arm.round ?? null;
  // A round still scoring has no file: its point reads off the channel's own cycle, as it streams.
  const { unfiled, read: roundRead, cfg: cfgOf, reading: readingOf } = useRound(path, round);
  const at = useMemo(
    () => (path && selected ? candidateSubject(path, selected.facts.individual) : ""),
    [path, selected],
  );
  const pickedCampaign = path?.at(-1)?.campaignId ?? "";
  const pipelineAt = useMemo(
    () => (at && pickedCampaign ? { campaignId: pickedCampaign, subject: at } : null),
    [at, pickedCampaign],
  );
  const id = selected?.facts.individual ?? null;
  const armKey = selected?.facts.reading.arm.key ?? null;
  const cfg = useMemo(
    () => (selected ? cfgOf(armKey, selected.label) : null),
    [cfgOf, armKey, selected],
  );
  const datasetName = reading?.dataset_name ?? null;
  const cellsPreset = useMemo<CellsPreset | null>(
    () => (path && id && datasetName ? { path, datasetName, candidateId: id, scope: "cycle" } : null),
    [path, id, datasetName],
  );
  const key = useMemo(
    () =>
      selected
        ? candidateSubject(pathOfAddress(selected.address), selected.facts.individual) || subject
        : subject,
    [selected, subject],
  );
  // This cycle's ledger only: a pick on a fork's lane was billed to a file this read did not open.
  const spentTo = useMemo(() => {
    if (!reading || !selected || round == null) return null;
    const onOwnCourse =
      encodeCyclePath(pathOfAddress(selected.address)) === encodeCyclePath(readingPath(reading));
    return onOwnCourse ? (reading.spend_to_round[String(round)] ?? null) : null;
  }, [reading, selected, round]);
  const overlay = useMemo(
    () => applyFlatEdits(cfg?.config ?? {}, editsFor(edits, key)),
    [cfg, edits, key],
  );
  const line = useMemo(() => (selected ? mainLineOf(index, selected) : []), [index, selected]);

  return {
    path,
    key,
    isOwn: !!reading && key === pointKeyOf(reading),
    reading: readingOf(armKey),
    cfg,
    cellsPreset,
    overlay,
    unfiled,
    roundRead,
    pipelineAt,
    spentTo,
    line,
  };
}
