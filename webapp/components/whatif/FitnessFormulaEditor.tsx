"use client";
import { WhatIfGrid } from "./WhatIfGrid";
import type { Row } from "./meta";
import type { BarSlot } from "./FitnessChart";

interface Props {
  rows: Row[];
  selected: Set<string>;
  inActive: Set<string>;
  weights: Record<string, number>;
  bars: BarSlot[];
  onToggle: (name: string) => void;
  onWeight: (name: string, w: number) => void;
}

// The what-if ablation editor: pick evaluators + reweight them to recompute
// the composite client-side. Extracted from FitnessPanel as the presentational
// formula-editor surface; the panel owns the {selected, weights} store and
// passes the realized state + handlers down. Pure renderer — all state lives
// upstream in the module store so a tab swap preserves it.
export function FitnessFormulaEditor({
  rows,
  selected,
  inActive,
  weights,
  bars,
  onToggle,
  onWeight,
}: Props) {
  return (
    <WhatIfGrid
      rows={rows}
      selected={selected}
      inActive={inActive}
      weights={weights}
      bars={bars}
      onToggle={onToggle}
      onWeight={onWeight}
    />
  );
}
