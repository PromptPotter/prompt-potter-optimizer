"use client";

import { useCallback, useMemo, useState } from "react";
import type { ArmNode, SubjectReading } from "@/lib/api";
import type { NodeSchemaReading } from "@/lib/types";
import { useCampaignPipeline } from "@/lib/hooks/useConnector";
import { candidateSubject, readingPath } from "@/lib/api/reads";
import {
  applyFlatEdits,
  candidateObserveConfig,
  candidatesAtPath,
  descendantsOf,
  historicalSamplesFor,
  indexLineage,
  mainLineOf,
  pathOf,
  pipelineReadStatus,
  scoreboardReading,
  type CompareItem,
  type LineageIndex,
  type RunGroup,
} from "@/lib/derivations";
import { readyData } from "@/lib/hooks/useRead";
import { useRound } from "@/lib/hooks/useRound";
import { useLineageTree } from "@/lib/lineage";
import { fmtMetricInterval, fmtMetricValue, shortId } from "@/lib/format";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";
import { editsFor, pointKeyOf, useScenarioEdits } from "./config-edit";

export type MetricUnit = Parameters<typeof fmtMetricValue>[0];

type Fold = "detail" | "map" | "setup";

function useChannelHead(
  index: LineageIndex,
  reading: SubjectReading | null,
  selected: ArmNode | null,
) {
  return useMemo(() => {
    const candidates = candidatesAtPath(index, reading && readingPath(reading));
    const own = candidates.find((c) => c.id === reading?.candidate_id) ?? null;
    const winner = candidates.find((c) => c.course_winner) ?? own;
    const latest = candidates.find((c) => c.course_latest) ?? null;
    // Same point only when the COURSE agrees too: a fork-contributed attempt keeps its own id.
    const sameAs = (a: ArmNode | null, b: ArmNode | null) =>
      !!a && !!b && a.id === b.id && encodeCyclePath(pathOf(a)) === encodeCyclePath(pathOf(b));
    const isWinner = sameAs(selected, winner);
    const isLatest = sameAs(selected, latest);
    const byValue: Record<string, ArmNode | null> = {
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
  run: RunGroup | null,
  unit: MetricUnit,
  seedOn: "winner" | "own",
) {
  const { edits } = useScenarioEdits();
  const subject = channel.subject;
  const campaignName = run?.campaign.label || shortId(channel.rootCampaignId);
  const rootCycle = run?.campaign.root_cycle_id ?? null;
  const rootPath = useMemo<CyclePath>(
    () => (rootCycle ? [{ campaignId: channel.rootCampaignId, cycleId: rootCycle }] : []),
    [rootCycle, channel.rootCampaignId],
  );

  const { root, loaded, failed } = useLineageTree(rootPath, rootPath.length > 0);
  const index = useMemo(() => indexLineage(root), [root]);
  const tree = useMemo(
    () => ({ root, index, loaded, failed, rootPath }),
    [root, index, loaded, failed, rootPath],
  );
  const [selected, setSelected] = useState<ArmNode | null>(null);
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

  const pickedPath = useMemo(() => (selected ? pathOf(selected) : null), [selected]);
  const round = selected?.reading.arm.round ?? null;
  const { doc, loading: docLoading } = useRound(folds.detail ? pickedPath : null, round);
  const at = useMemo(
    () => (pickedPath && selected ? candidateSubject(pickedPath, selected.id) : ""),
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
  const docId = selected?.id ?? null;
  const pickedReading = docId ? scoreboardReading(doc, docId) : null;
  const cfg = selected
    ? candidateObserveConfig(doc, selected.reading.arm.label, selected.label)
    : null;
  const samples = useMemo(
    () => (docId && round != null ? historicalSamplesFor(doc, round, docId) : []),
    [doc, docId, round],
  );
  const key = useMemo(
    () => (selected ? candidateSubject(pathOf(selected), selected.id) || subject : subject),
    [selected, subject],
  );
  // This cycle's ledger only: a pick on a fork's lane was billed to a file this read did not open.
  const spentTo = useMemo(() => {
    if (!reading || !selected || round == null) return null;
    const onOwnCourse =
      encodeCyclePath(pathOf(selected)) === encodeCyclePath(readingPath(reading));
    return onOwnCourse ? (reading.spend_to_round[String(round)] ?? null) : null;
  }, [reading, selected, round]);
  const overlay = useMemo(
    () => applyFlatEdits(cfg?.config ?? {}, editsFor(edits, key)),
    [cfg, edits, key],
  );

  const edited = useMemo(() => {
    const seeds: string[] = [];
    for (const { candidates } of index.values()) {
      for (const c of candidates) {
        if (editsFor(edits, candidateSubject(pathOf(c), c.id)).size > 0) seeds.push(c.id);
      }
    }
    return seeds;
  }, [index, edits]);
  const invalidated = useMemo(() => descendantsOf(root, edited), [root, edited]);

  const withdrawn = reading !== null && invalidated.has(reading.candidate_id);
  const level = reading && {
    value: withdrawn ? "?" : fmtMetricValue(unit, reading.value),
    band: withdrawn
      ? "? · ? cells"
      : `${fmtMetricInterval(unit, reading.ci_lo, reading.ci_hi)} · ${reading.n_cells} cell${
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
      samples,
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
