"use client";
import { useState } from "react";
import type { DraftCampaignWire, DraftPatch } from "@/lib/api";
import {
  candidateObserveConfig,
  liveObserveConfig,
  roundHasCandidates,
  sortedRounds,
  wasElected,
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
//     read-only. The live ↔ historical selector sits ABOVE the surface — it picks
//     WHICH searchpoint the box shows; the box itself always renders exactly one
//     runnable spec.
// AUTHOR (lock editing) is a setup act; OBSERVE (read-only resolved config) is a
// run act — so a concrete node during a run shows OBSERVE, never the lock editor.
// Both OBSERVE states read ONE server-resolved field (`resolved_pipeline_params`)
// — never a client re-merge. Steering is a separate act with its own home
// (`ScoringInspector` → `SteerForkPanel`); this never forks.
//
// The ORIGIN is not a state here. C0 is a candidate of round 0 and observes
// through the historical path by `candidate_id`, exactly like C2.3.

interface Props {
  // The active draft while a campaign is being set up; null otherwise.
  draft: DraftCampaignWire | null;
  onClose: () => void;
  // Setup only: makes the LLM node's prompt editable (persists via this patch).
  onPromptApply?: (patch: DraftPatch) => void;
}

const OBSERVE_STATES: ObserveState[] = ["live", "historical"];

// The historical observe target: a specific past candidate located by id in its
// round file. `{round, candidateId, label}` — the shape `candidateObserveConfig`
// reads.
interface HistTarget {
  round: number;
  candidateId: string;
  label: string;
}

// The last completed round's incumbent — the default "historical" observe target
// when the operator hasn't selected a candidate. Round 0 is INCLUDED: on a
// campaign that has only measured its origin, C0 is the last completed
// searchpoint, and it is reached the same way every other one is. It is only
// badged `winner` when the round it won had rivals (`wasElected`) — round 0
// crowns its sole arm by default and must not claim an election.
function lastIncumbent(dash: DashboardSnapshot | null): HistTarget | null {
  const rounds = sortedRounds(dash).filter(roundHasCandidates).reverse();
  for (const r of rounds) {
    const idx = r.candidates.findIndex((c) => c.is_winner);
    const w = idx >= 0 ? r.candidates[idx] : null;
    if (w?.candidate_id) {
      const label = candidateLabel(r.round, idx);
      return {
        round: r.round,
        candidateId: w.candidate_id,
        label: wasElected(true, r.candidates.length) ? `winner · ${label}` : label,
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
  // otherwise the historical observe-config fetch below starves and the
  // panel blanks even though the round files exist on disk. It follows the
  // viewed leaf, so an L4 inner loop reads the inner cycle's round files.
  // `liveObserveConfig(dash)` above stays on `dash`: live-snapshot data,
  // correctly sourced.
  const { viewedPath } = useWorkspace();

  // Historical target: the operator's selected candidate if any — C0 included, it
  // is a candidate of round 0 — else the last completed round's incumbent. ONE
  // fetch, following the target.
  const histTarget: HistTarget | null = selCand
    ? { round: selCand.round, candidateId: selCand.candidate_id, label: `selected · ${selCand.label}` }
    : lastIncumbent(dash);
  const runObserve = !draft;
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

  // --- OBSERVE (run): one read-only resolved config, selected across the two
  // searchpoint states. Each reads the served `resolved_pipeline_params`. A
  // concrete node scopes the header/prompt; the config stays whole-pipeline.
  const histCfg = histTarget
    ? candidateObserveConfig(histFile.doc, histTarget.candidateId, histTarget.label, nodeId)
    : null;
  // `live` is available only while the cycle is actually running — `current_round`
  // candidates linger in dashboard.json after a stop, so gate on `isLive`, not on
  // their mere presence (else a stopped run still offers a stale "live").
  const avail: Record<ObserveState, boolean> = {
    live: isLive && !!liveCfg,
    historical: !!histCfg,
  };
  // Auto-follow the operator's selection to that candidate's own round file. A
  // candidate in the STILL-LIVE round has no round file yet (it's written at round
  // close), so fall to the live searchpoint — the one actually running. With no
  // selection, prefer the in-flight candidate, else the last completed one.
  const auto: ObserveState = selCand
    ? avail.historical
      ? "historical"
      : "live"
    : avail.live
      ? "live"
      : "historical";
  const state: ObserveState = pref && avail[pref] ? pref : auto;
  // Null only when NOTHING has been measured yet — no closed round, nothing in
  // flight. Say so; never seed the surface with `{}`, which draws an empty config
  // table that reads as "this program has no params" rather than "not resolved yet".
  const cfg = avail[state] ? (state === "live" ? liveCfg : histCfg) : null;

  const toggle =
    avail.live || avail.historical ? (
      <div className="observe-toggle">
        <span className="observe-toggle-label">View</span>
        <SegmentedControl<ObserveState>
          options={OBSERVE_STATES.map((s) => ({ value: s, label: s, disabled: !avail[s] }))}
          value={state}
          onChange={setPref}
          ariaLabel="Searchpoint view — live or historical"
        />
      </div>
    ) : null;

  return (
    <>
      {toggle}
      {cfg ? (
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
      ) : (
        // Nothing has been MEASURED yet — no closed round, nothing in flight. Name
        // the state rather than implying a fetch that never lands: on a fresh
        // campaign this is the whole of round 0, which on an L4 run is hours.
        <p className="inspector-note">
          {isLive
            ? "Scoring in progress — the resolved spec appears when the round closes."
            : "No measured searchpoint yet."}
        </p>
      )}
    </>
  );
}
