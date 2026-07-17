"use client";
import { useState } from "react";
import type { DraftCampaignWire, DraftPatch } from "@/lib/api";
import {
  candidateObserveConfig,
  liveObserveConfig,
  originObserveConfig,
  roundHasCandidates,
  sortedRounds,
  type ObserveConfig,
  type ObserveState,
} from "@/lib/derivations";
import { candidateLabel } from "@/lib/candidate-label";
import { useSelection } from "@/lib/SelectionContext";
import { useWorkspace } from "@/lib/workspace";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useConnector } from "@/lib/hooks/useConnector";
import { useRoundFile } from "@/lib/hooks/useRoundFile";
import type { DashboardSnapshot } from "@/lib/poll";
import { SegmentedControl } from "@/components/ui";
import { NodeSurface } from "./NodeSurface";

// Detail for the node clicked in the pipeline. It dispatches on LIFECYCLE, not on
// node-presence:
//   - setup (a draft is active) + a concrete node → AUTHOR its search-space config
//     (the draft-backed lock/allow editor) + prompt + output;
//   - setup + the whole-pipeline chip → read-only draft preview (no toggle);
//   - a RUN (no draft) → OBSERVE the resolved config the searchpoint executes,
//     read-only. The 3-way origin ↔ live ↔ historical selector sits ABOVE the
//     surface — it picks WHICH searchpoint the box shows; the box itself always
//     renders exactly one runnable spec.
// AUTHOR (lock editing) is a setup act; OBSERVE (read-only resolved config) is a
// run act — so a concrete node during a run shows OBSERVE, never the lock editor.
// All three OBSERVE states read ONE server-resolved field
// (`resolved_pipeline_params`) — never a client re-merge. Steering is a separate
// act with its own home (`ScoringInspector` → `SteerForkPanel`); this never forks.

interface Props {
  // The active draft while a campaign is being set up; null otherwise.
  draft: DraftCampaignWire | null;
  onClose: () => void;
  // Setup only: makes the LLM node's prompt editable (persists via this patch).
  onPromptApply?: (patch: DraftPatch) => void;
}

const OBSERVE_STATES: ObserveState[] = ["origin", "live", "historical"];

// The historical observe target: a specific past candidate located by id in its
// round file. `{round, candidateId, label}` — the shape `candidateObserveConfig`
// reads.
interface HistTarget {
  round: number;
  candidateId: string;
  label: string;
}

// The last completed round (≥1) carrying a flagged winner — the default
// "historical" observe target when the operator hasn't selected a candidate.
// Round 0 is excluded (it == origin). Null when no such round has closed yet.
function lastWinner(dash: DashboardSnapshot | null): HistTarget | null {
  const rounds = sortedRounds(dash)
    .filter((r) => r.round >= 1 && roundHasCandidates(r))
    .reverse();
  for (const r of rounds) {
    const idx = r.candidates.findIndex((c) => c.is_winner);
    const w = idx >= 0 ? r.candidates[idx] : null;
    if (w?.candidate_id) {
      return {
        round: r.round,
        candidateId: w.candidate_id,
        label: `winner · ${candidateLabel(r.round, idx)}`,
      };
    }
  }
  return null;
}

export function BackendNodeDetail({ draft, onClose, onPromptApply }: Props) {
  // `cv` self-sourced from the nearest ConnectorProvider.
  const cv = useConnector();
  const { dash, isLive } = useDashboard();
  const { node: selected, candidate: selCand } = useSelection();
  // Target-scoped selections only — this panel renders the BACKEND node. An
  // optimizer-scoped id could otherwise match a same-named target node (pp-self
  // declares `l1_generate` on both canvases).
  const selectedId = selected?.scope === "target" ? selected.id : null;
  const node = cv.view?.nodes.find((n) => n.id === selectedId && n.kind !== "io") ?? null;

  // The selected node id scopes prompt resolution: a meta-prompt node (pp-self's
  // l1_generate / l1_critique / …) carries its evolved prompt per-node inside the
  // resolved params, not in the flat `prompt_fields`, so pass it through so the
  // observe read model can surface THIS node's evolved fields.
  const nodeId = node?.id ?? null;
  const liveCfg = liveObserveConfig(dash, nodeId);
  // Viewed leaf path from the synchronous workspace, NOT from `dash` (which
  // hard-nulls on a soft unit switch and stays null during `warming_up`) —
  // otherwise the round-0 / historical observe-config fetches below starve and
  // the panel blanks even though the round files exist on disk. It follows the
  // viewed leaf, so an L4 inner loop reads the inner cycle's round files.
  // `liveObserveConfig(dash)` above stays on `dash`: live-snapshot data,
  // correctly sourced.
  const { viewedPath } = useWorkspace();

  // Historical target: the operator's selected candidate (round ≥1) if any, else
  // the last completed round's winner. The run-observe branch reads its round file.
  const histTarget: HistTarget | null =
    selCand && selCand.round >= 1
      ? { round: selCand.round, candidateId: selCand.candidate_id, label: `selected · ${selCand.label}` }
      : lastWinner(dash);
  const runObserve = !draft;
  const round0 = useRoundFile(viewedPath, runObserve ? 0 : null);
  const histFile = useRoundFile(viewedPath, runObserve ? histTarget?.round ?? null : null);

  // The explicit toggle pref, reset whenever the selected candidate changes so the
  // panel re-follows the new selection (render-phase guarded reset).
  const [pref, setPref] = useState<ObserveState | null>(null);
  const selKey = selCand?.candidate_id ?? null;
  const [prevSel, setPrevSel] = useState<string | null>(selKey);
  if (selKey !== prevSel) {
    setPrevSel(selKey);
    setPref(null);
  }

  // --- AUTHOR (setup): a concrete node WITH an active draft. The search-space
  // lock/allow editor; persists to the draft overlay. Reachable only during setup.
  if (draft && node) {
    return (
      <NodeSurface
        node={node}
        point={{
          origin_prompt_fields: draft.origin_prompt_fields,
          pipeline_overlay: {},
        }}
        configSeed={draft.pipeline_overlay}
        schema={cv.nodeConfigSchema}
        outputSchema={cv.nodeOutputSchema}
        label="draft — setup"
        mode="search-space"
        onClose={onClose}
        onApply={onPromptApply}
      />
    );
  }

  // --- OBSERVE (draft, whole pipeline): the draft IS the origin being authored;
  // no server resolved config exists pre-mint, so show the draft prompt +
  // schema-declared config, read-only, no toggle.
  if (draft) {
    return (
      <NodeSurface
        node={null}
        point={{ origin_prompt_fields: draft.origin_prompt_fields, pipeline_overlay: {} }}
        configSeed={{}}
        schema={cv.nodeConfigSchema}
        outputSchema={cv.nodeOutputSchema}
        label="draft — setup"
        mode="values"
        readOnly
        onClose={onClose}
      />
    );
  }

  // --- OBSERVE (run): one read-only resolved config, selected across the three
  // searchpoint states. Each reads the served `resolved_pipeline_params`; the
  // origin fallback (round 0 not yet written) is the schema-declared config. A
  // concrete node scopes the header/prompt; the config stays whole-pipeline.
  const histCfg = histTarget
    ? candidateObserveConfig(histFile.doc, histTarget.candidateId, histTarget.label, nodeId)
    : null;
  const originCfg = originObserveConfig(round0.doc, nodeId);
  // `live` is available only while the cycle is actually running — `current_round`
  // candidates linger in dashboard.json after a stop, so gate on `isLive`, not on
  // their mere presence (else a stopped run still offers a stale "live").
  const avail: Record<ObserveState, boolean> = {
    origin: true,
    live: isLive && !!liveCfg,
    historical: !!histCfg,
  };
  // Auto-follow the operator's selection (a selected candidate → its searchpoint);
  // else the live in-flight candidate while running; else the last completed
  // searchpoint (historical); else origin. A selected candidate in the STILL-LIVE
  // round has no round file yet (it's written at round close), so its historical
  // config is unavailable — fall to the live searchpoint, never origin (which would
  // wrongly show the dataset floor instead of the candidate actually running).
  const noSelection: ObserveState = avail.live
    ? "live"
    : avail.historical
      ? "historical"
      : "origin";
  const auto: ObserveState = !selCand
    ? noSelection
    : selCand.round < 1
      ? "origin"
      : avail.historical
        ? "historical"
        : avail.live
          ? "live"
          : "origin";
  const state: ObserveState = pref && avail[pref] ? pref : avail[auto] ? auto : "origin";
  // Origin's resolved config is born at round 0; until that file lands it is genuinely
  // unknown. Don't present the empty config as the resolved origin program (that's the
  // retired `{}` origin-fake, searchPoint.ts) — when running and round 0 hasn't been
  // written, label it as resolving so the blank reads "not yet resolved", not "no params".
  const originResolving = isLive && originCfg == null;
  const fallbackOrigin: ObserveConfig = {
    promptFields: cv.originPromptFields ?? {},
    config: {},
    label: originResolving ? "origin — resolving (round 0)" : "origin",
  };
  const cfg =
    (state === "live" ? liveCfg : state === "historical" ? histCfg : originCfg) ?? fallbackOrigin;

  const toggle =
    avail.live || avail.historical ? (
      <div className="observe-toggle">
        <span className="observe-toggle-label">View</span>
        <SegmentedControl<ObserveState>
          options={OBSERVE_STATES.map((s) => ({ value: s, label: s, disabled: !avail[s] }))}
          value={state}
          onChange={setPref}
          ariaLabel="Searchpoint view — origin, live, or historical"
        />
      </div>
    ) : null;

  return (
    <>
      {toggle}
      <NodeSurface
        node={node}
        point={{ origin_prompt_fields: cfg.promptFields, pipeline_overlay: {} }}
        configSeed={cfg.config}
        schema={cv.nodeConfigSchema}
        outputSchema={cv.nodeOutputSchema}
        label={cfg.label}
        mode="values"
        readOnly
        onClose={onClose}
      />
    </>
  );
}
