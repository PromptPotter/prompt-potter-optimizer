"use client";
import { Icon, SegmentedControl } from "@/components/ui";
import { useWorkspace } from "@/lib/workspace";
import { PRESETS } from "./families";
import { preloadLazyPanes } from "./lazy-panes";

const SEGMENTS = PRESETS.map((p) => ({
  value: p.label,
  label: (
    <>
      <Icon size={16} viewBox="0 0 16 16" strokeWidth={1.3}>
        {p.glyph}
      </Icon>
      {p.label}
    </>
  ),
}));

// A preset presses only while the open views are exactly its families.
export function ViewTabs() {
  const { views, open, close } = useWorkspace();
  const lit = PRESETS.find(
    (p) => p.families.length === views.length && views.every((v) => p.families.includes(v.family)),
  );
  const show = (label: string) => {
    const preset = PRESETS.find((p) => p.label === label);
    if (!preset) return;
    for (const family of preset.families) open(family);
    for (const v of views) if (!preset.families.includes(v.family)) close(v.family);
  };

  return (
    <nav
      className="view-tabs"
      aria-label="Campaign view"
      onPointerEnter={preloadLazyPanes}
      onFocus={preloadLazyPanes}
    >
      <SegmentedControl
        size="lg"
        options={SEGMENTS}
        value={lit?.label ?? null}
        onChange={show}
        ariaLabel="Campaign view"
      />
    </nav>
  );
}
