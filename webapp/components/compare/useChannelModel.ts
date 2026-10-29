"use client";

import { useCallback, useMemo, useState } from "react";
import type { HierarchyCandidate, SubjectReading } from "@/lib/api";
import type { NodeSchemaReading } from "@/lib/types";
import { useCampaignPipeline } from "@/lib/hooks/useConnector";
import { candidateSubject, readingPath } from "@/lib/api/reads";
import {
  applyFlatEdits,
  candidateObserveConfig,
  candidatesAtPath,
  descendantsOf,
  indexLineage,
  mainLineOf,
  pipelineReadStatus,
  scoreboardReading,
  type CompareItem,
  type LineageIndex,
} from "@/lib/derivations";
import type { HierarchyCampaign } from "@/lib/api";
import { usePresetCells, type CellsPreset } from "@/lib/hard-samples";
import { readyData } from "@/lib/hooks/useRead";
import { useRound } from "@/lib/hooks/useRound";
import { useLineageTree } from "@/lib/lineage";
import { fmtInterval, fmtUnit, shortId, type MetricUnit } from "@/lib/format";
import { type CyclePath, decodeCyclePath, encodeCyclePath, pathOfAddress } from "@/lib/ids";
import { editsFor, pointKeyOf, useScenarioEdits } from "./config-edit";

type Fold = "detail" | "map" | "setup";

function useChannelHead(
  index: LineageIndex,
  reading: SubjectReading | null,
  selected: HierarchyCandidate | null,
) {
  return useMemo(() => {
    const candidates = candidatesAtPath(index, reading && readingPath(reading));
    // On the reading's own course: a repair re-measures an individual another course answers for.
    const mine = reading ? candidates.filter((c) => c.facts.individual === reading.candidate_id) : [];
    const own = mine.find((c) => c.facts.answers_for_id) ?? mine[0] ?? null;
    const winner = candidates.find((c) => c.facts.stands) ?? own;
    const latest = candidates.find((c) => c.facts.course_latest) ?? null;
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
  }, [index, reading, selected]);
}

export function useChannelModel(
  { channel, reading }: CompareItem,
  run: HierarchyCampaign | null,
  unit: MetricUnit,
  seedOn: "winner" | "own",
) {
  const { edits } = useScenarioEdits();
  const subject = channel.subject;
  const campaignName = run?.label || shortId(channel.rootCampaignId);
  const rootAddress = run?.facts.root ?? null;
  const rootPath = useMemo<CyclePath>(
    () => (rootAddress ? (decodeCyclePath(rootAddress) ?? []) : []),
    [rootAddress],
  );

  const { root, loaded, failed } = useLineageTree(run?.address ?? "", rootPath.length > 0);
  const index = useMemo(() => indexLineage(root), [root]);
  const tree = useMemo(
    () => ({ root, index, loaded, failed, rootPath }),
    [root, index, loaded, failed, rootPath],
  );
  const [selected, setSelected] = useState<HierarchyCandidate | null>(null);
  const head = useChannelHead(index, reading, selected);
  const [seededFor, setSeededFor] = useState<string | null>(null);
  const seed = seedOn === "winner" ? head.nodeFor("winner") : head.own;
  if (seed && seededFor !== subject) {
    setSeededFor(subject);
    setSelected(seed);
  }
  const [folds, setFolds] = useState<Record<Fold, boolean>>(() => ({
    detail: reading?.kind === "candidate",
    map: false,
    setup: true,
  }));
  const toggleFold = useCallback(
    (fold: Fold) => setFolds((prev) => ({ ...prev, [fold]: !prev[fold] })),
    [],
  );

  const pickedPath = useMemo(() => (selected ? pathOfAddress(selected.address) : null), [selected]);
  const round = selected?.facts.reading.arm.round ?? null;
  const { doc, loading: docLoading } = useRound(folds.detail ? pickedPath : null, round);
  const at = useMemo(
    () => (pickedPath && selected ? candidateSubject(pickedPath, selected.facts.individual) : ""),
    [pickedPath, selected],
  );
  const pickedCampaign = pickedPath?.at(-1)?.campaignId ?? "";
  const pipelineRead = useCampaignPipeline(folds.detail && at ? pickedCampaign || null : null, at);
  const pipeline = readyData(pipelineRead);
  const pipelineStatus = pipelineReadStatus({
    bound: pipelineRead.status !== "idle",
    loading: pipelineRead.status === "loading",
    failed: pipelineRead.status === "failed",
  });
  const schema = useMemo<NodeSchemaReading>(
    () => ({
      status: pipelineStatus,
      config: pipeline?.node_config_schema ?? null,
      output: pipeline?.node_output_schema ?? null,
      isSingleNode: !!pipeline?.is_single_node,
    }),
    [pipelineStatus, pipeline],
  );
  const line = useMemo(() => (selected ? mainLineOf(index, selected) : []), [index, selected]);
  const docId = selected?.facts.individual ?? null;
  const pickedReading = docId ? scoreboardReading(doc, docId) : null;
  const cfg = selected
    ? candidateObserveConfig(doc, selected.facts.reading.arm.key, selected.label)
    : null;
  const datasetName = reading?.dataset_name ?? null;
  const cellsPreset = useMemo<CellsPreset | null>(
    () =>
      pickedPath && docId && datasetName
        ? { path: pickedPath, datasetName, candidateId: docId, scope: "cycle" }
        : null,
    [pickedPath, docId, datasetName],
  );
  const { cells } = usePresetCells(folds.detail ? cellsPreset : null);
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

  const edited = useMemo(() => {
    const seeds: string[] = [];
    for (const c of index.candidates) {
      const id = c.facts.individual;
      if (editsFor(edits, candidateSubject(pathOfAddress(c.address), id)).size > 0) seeds.push(id);
    }
    return seeds;
  }, [index, edits]);
  const invalidated = useMemo(() => descendantsOf(index, edited), [index, edited]);

  const withdrawn = reading !== null && invalidated.has(reading.candidate_id);
  const level = reading && {
    value: withdrawn ? "?" : fmtUnit(unit, reading.value),
    band: withdrawn
      ? "? · ? cells"
      : `${fmtInterval(unit, reading.ci_lo, reading.ci_hi)} · ${reading.n_cells} cell${
          reading.n_cells === 1 ? "" : "s"
        }`,
  };

  return {
    subject,
    campaignName,
    tree,
    selected,
    setSelected,
    head,
    folds,
    toggleFold,
    picked: {
      path: pickedPath,
      id: docId,
      key,
      isOwn: !!reading && key === pointKeyOf(reading),
      reading: pickedReading,
      cfg,
      cellsPreset,
      cells,
      overlay,
      docLoading,
      schema,
    },
    line,
    spentTo,
    invalidated,
    withdrawn,
    level,
  };
}

export type ChannelModel = ReturnType<typeof useChannelModel>;
