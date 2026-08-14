"use client";
import type { DraftCampaignWire, DraftPatch } from "@/lib/api";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useConnector } from "@/lib/hooks/useConnector";
import { useObserveSearchPoint } from "@/lib/hooks/useObserveSearchPoint";
import { useSelection } from "@/lib/SelectionContext";
import { interiorNodes, observeOptions, type ObserveState } from "@/lib/derivations";
import { SegmentedControl } from "@/components/ui";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";

// Detail for the node clicked in the pipeline. It dispatches on LIFECYCLE, not on
// node-presence:
//   - setup (a draft is active) + a concrete node → AUTHOR its search-space config
//     (the draft-backed lock/allow editor) + prompt + output;
//   - setup + the whole-pipeline chip → read-only draft preview (no toggle);
//   - a RUN (no draft) → OBSERVE the resolved config the searchpoint executes,
//     read-only. The best / most-recent / selected picker sits ABOVE the surface — it
//     picks WHICH searchpoint the box shows; the box itself always renders exactly one
//     runnable spec. That resolution is shared with the chat's run card and lives in
//     `useObserveSearchPoint`, so the two hosts cannot disagree about what "best" means.
// AUTHOR (lock editing) is a setup act; OBSERVE (read-only resolved config) is a
// run act — so a concrete node during a run shows OBSERVE, never the lock editor.
// Every OBSERVE state reads ONE server-resolved field (`resolved_pipeline_params`)
// — never a client re-merge. Steering is a separate act with its own home
// (`ScoringInspector` → `SteerForkPanel`); this never forks.
//
// The ORIGIN is not a state here. C0 is a candidate of round 0 and resolves through
// the same positional join every other candidate does.

interface Props {
  // The active draft while a campaign is being set up; null otherwise.
  draft: DraftCampaignWire | null;
  onClose: () => void;
  // Setup only: makes the LLM node's prompt editable (persists via this patch).
  onPromptApply?: (patch: DraftPatch) => void;
}

export function BackendNodeDetail({ draft, onClose, onPromptApply }: Props) {
  // `cv` self-sourced from the nearest ConnectorProvider.
  const cv = useConnector();
  const { isLive } = useDashboard();
  const { node: selected } = useSelection();
  // Target-scoped selections only — this panel renders the BACKEND node. An
  // optimizer-scoped id could otherwise match a same-named target node (pp-self
  // declares `l1_generate` on both canvases).
  const selectedId = selected?.scope === "target" ? selected.id : null;
  const node = interiorNodes(cv.view).find((n) => n.id === selectedId) ?? null;

  // The selected node id scopes prompt resolution: an optimizer prompt node (pp-self's
  // l1_generate / l1_critique / …) carries its evolved prompt per-node inside the
  // resolved params, not in the flat `prompt_fields`, so pass it through so the
  // observe read model can surface THIS node's evolved fields.
  const nodeId = node?.id ?? null;
  // Parked while a draft is being authored: there is no measured searchpoint to
  // observe yet, and the fetch would land on whichever cycle sits behind the draft.
  const observe = useObserveSearchPoint(nodeId, !draft);

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

  // --- OBSERVE (run): one read-only resolved config, picked across the three
  // searchpoint states. Each reads the served `resolved_pipeline_params`. A
  // concrete node scopes the header/prompt; the config stays whole-pipeline.
  // A one-option group is not a choice — with a single state available the
  // surface's own label ("best · C2.1") already names what is on screen.
  const options = observeOptions(observe.avail);
  const toggle =
    options.length > 1 ? (
      <div className="observe-toggle">
        <span className="observe-toggle-label">Searchpoint</span>
        <SegmentedControl<ObserveState>
          options={options}
          value={observe.state}
          onChange={observe.setPref}
          ariaLabel="Which searchpoint to show"
        />
      </div>
    ) : null;

  return (
    <>
      {toggle}
      {observe.cfg ? (
        <NodeSurface
          node={node}
          point={{ origin_prompt_fields: observe.cfg.promptFields, pipeline_overlay: {} }}
          configSeed={observe.cfg.config}
          schema={cv.nodeConfigSchema}
          outputSchema={cv.nodeOutputSchema}
          label={observe.cfg.label}
          mode="values"
          readOnly
          onClose={onClose}
        />
      ) : (
        // Three different absences, each said differently. A round file still in
        // flight is NOT "nothing measured", and on a fresh campaign the empty state
        // is the whole of round 0 — hours on an L4 run — so it must not imply a
        // fetch that never lands, nor seed the surface with `{}` (an empty config
        // table reads as "this program has no params").
        <p className="inspector-note">
          {observe.loading
            ? "Loading the searchpoint…"
            : isLive
              ? "Scoring in progress — the resolved spec appears when the round closes."
              : "No measured searchpoint yet."}
        </p>
      )}
    </>
  );
}
