"use client";
// L4 Lab — the developer surface for tuning the Potter's own meta-prompts.
// NOT part of the whitelabeled end-user product: end-users use the optimizer;
// they never tune it. The Lab produces the champion `_optimizer_meta/prompts.json`
// that every user then consumes silently. Shown only when the tenant has pp-self
// cycles on disk (AppShell's `hasL4Data` gate).
//
// Slice 1 mounts the Champion Console; the Capability Matrix (resource cells) and
// the Outer Verdict forest plot land alongside it in later L4 slices.

import type { ChampionRegistryResponse } from "@/lib/api";
import { ChampionConsole } from "@/components/lab/ChampionConsole";
import { CapabilityMatrixPanel } from "@/components/lab/CapabilityMatrixPanel";
import { OuterVerdictPanel } from "@/components/lab/OuterVerdictPanel";

export function LabPane({
  registry,
  onOpenCycle,
}: {
  registry: ChampionRegistryResponse | null;
  onOpenCycle?: (campaignId: string, cycleId: string) => void;
}) {
  return (
    <div className="content lab-pane">
      <header className="lab-head">
        <h1>L4 Lab</h1>
        <p>
          Optimizing the Potter&rsquo;s own meta-prompts. Developer surface — the reigning
          champion here is what the shipped optimizer starts from.
        </p>
      </header>
      <CapabilityMatrixPanel />
      <OuterVerdictPanel />
      {registry ? (
        <ChampionConsole registry={registry} onOpenCycle={onOpenCycle} />
      ) : (
        <p className="lab-empty">Loading champion registry…</p>
      )}
    </div>
  );
}
