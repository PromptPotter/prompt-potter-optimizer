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
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useConnector } from "@/lib/hooks/useConnector";
import { useRoundFile } from "@/lib/hooks/useRoundFile";
import { cx } from "@/lib/cx";
import type { DashboardSnapshot } from "@/lib/poll";
import { NodeSurface } from "./NodeSurface";

// Detail for the node clicked in the pipeline. It dispatches on LIFECYCLE, not on
// node-presence:
//   - setup (a draft is active) + a concrete node → AUTHOR its search-space config
//     (the draft-backed lock/allow editor) + prompt + output;
//   - setup + the whole-pipeline chip → read-only draft preview (no toggle);
//   - a RUN (no draft) → OBSERVE the resolved config the searchpoint executes,
//     read-only, with a 3-way origin ↔ live ↔ historical toggle, scoped to the
//     selected node's header/prompt.
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
  const rounds = sortedRounds(dash).filter((r) => r.round >= 1 && roundHasCandidates(r));
  for (let i = rounds.length - 1; i >= 0; i--) {
    const r = rounds[i];
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
  const { node: selectedId, candidate: selCand } = useSelection();
  const node = cv.view?.nodes.find((n) => n.id === selectedId && n.kind !== "io") ?? null;

  const liveCfg = liveObserveConfig(dash);
  const campaignId = dash?.campaign_id ?? null;
  const cycleId = dash?.cycle_id ?? null;

  // Historical target: the operator's selected candidate (round ≥1) if any, else
  // the last completed round's winner. The run-observe branch reads its round file.
  const histTarget: HistTarget | null =
    selCand && selCand.round >= 1
      ? { round: selCand.round, candidateId: selCand.candidate_id, label: `selected · ${selCand.label}` }
      : lastWinner(dash);
  const runObserve = !draft;
  const round0 = useRoundFile(campaignId, cycleId, runObserve ? 0 : null);
  const histFile = useRoundFile(campaignId, cycleId, runObserve ? histTarget?.round ?? null : null);

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
          origin_prompt_fields: draft.origin_prompt_fields ?? {},
          pipeline_overlay: {},
        }}
        configSeed={(draft.pipeline_overlay ?? {}) as Record<string, unknown>}
        schema={cv.nodeConfigSchema}
        outputSchema={cv.nodeOutputSchema}
        label="draft — setup"
        mode="search-space"
        lockModel={draft.optimization_overrides.lock_model}
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
        point={{ origin_prompt_fields: draft.origin_prompt_fields ?? {}, pipeline_overlay: {} }}
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
    ? candidateObserveConfig(histFile.doc, histTarget.candidateId, histTarget.label)
    : null;
  const originCfg = originObserveConfig(round0.doc);
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
  // searchpoint (historical); else origin.
  const auto: ObserveState = selCand
    ? selCand.round >= 1
      ? "historical"
      : "origin"
    : avail.live
      ? "live"
      : avail.historical
        ? "historical"
        : "origin";
  const state: ObserveState = pref && avail[pref] ? pref : avail[auto] ? auto : "origin";
  const fallbackOrigin: ObserveConfig = {
    promptFields: cv.originPromptFields ?? {},
    config: {},
    label: isLive ? "origin — running" : "origin",
  };
  const cfg =
    (state === "live" ? liveCfg : state === "historical" ? histCfg : originCfg) ?? fallbackOrigin;

  const toggle =
    avail.live || avail.historical ? (
      <div className="config-row observe-toggle">
        <span className="config-label">View</span>
        <span className="config-value">
          {OBSERVE_STATES.map((s) => (
            <button
              key={s}
              type="button"
              className={cx(
                "config-level",
                state === s ? "is-on" : "is-off",
                !avail[s] && "is-disabled",
              )}
              disabled={!avail[s]}
              aria-pressed={state === s}
              onClick={() => setPref(s)}
            >
              {s}
            </button>
          ))}
        </span>
      </div>
    ) : null;

  return (
    <NodeSurface
      node={node}
      point={{ origin_prompt_fields: cfg.promptFields, pipeline_overlay: {} }}
      configSeed={cfg.config}
      schema={cv.nodeConfigSchema}
      outputSchema={cv.nodeOutputSchema}
      label={cfg.label}
      mode="values"
      toggle={toggle}
      readOnly
      onClose={onClose}
    />
  );
}
