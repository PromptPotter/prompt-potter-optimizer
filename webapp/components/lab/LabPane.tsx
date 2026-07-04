"use client";
// L4 Lab — the developer surface for tuning the Potter's own meta-prompts.
// NOT part of the whitelabeled end-user product: end-users use the optimizer;
// they never tune it. The Lab elects a champion meta-prompt state; `champion apply`
// graduates it into the distributable `_optimizer/pipeline.json` that every user
// then consumes silently. Shown only to a dev identity holding `L4_LAB_CAP`
// (AppShell's capability gate, off `/auth/me`).
//
// Three panels: the Capability Matrix (operator-set model×dataset cells), the Outer
// Verdict forest plot (per-round blocked-paired read), and the Champion Console.

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
