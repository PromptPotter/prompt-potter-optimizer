"use client";
import { useWorkspace } from "@/lib/workspace";
import type { CycleListEntry } from "@/lib/api";

// Inline campaign picker for the dashboard breadcrumb. The breadcrumb text
// becomes a `<select>` styled to read as text — the operator clicks the
// cycle id and gets a native dropdown grouped by dataset_name. Native
// control = free keyboard nav + click-outside-to-close + a11y.
//
// Campaign list, active pointer, and the current selection all come from
// the shared workspace context — no independent fetch, no manual refresh
// button: the workspace poll keeps the list current.
export function CyclePicker() {
  const { cycleId, cycles, cyclesLoaded, cyclesError, activeCycleId, selectCycle } =
    useWorkspace();

  if (cyclesError && cycles.length === 0) {
    return <span className="cycle-picker-err">campaigns: {cyclesError}</span>;
  }
  if (!cyclesLoaded) {
    return <span>{cycleId || "loading…"}</span>;
  }
  if (cycles.length === 0) {
    return <span>{cycleId || "no campaigns"}</span>;
  }

  // Group by dataset_name (alpha), within each group sort by updated_at desc.
  const groups = new Map<string, CycleListEntry[]>();
  for (const c of cycles) {
    const key = c.dataset_name || "(unknown)";
    const arr = groups.get(key) ?? [];
    arr.push(c);
    groups.set(key, arr);
  }
  for (const arr of groups.values()) {
    arr.sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
  }
  const groupKeys = Array.from(groups.keys()).sort();

  // If the selected cycleId isn't in the list (deleted dir, deep-linked to a
  // missing cycle), render it as a disabled-prefixed marker so the operator
  // can see the mismatch without the picker silently re-snapping to another.
  const selectedKnown = cycles.some((c) => c.cycle_id === cycleId);

  return (
    <span className="cycle-picker">
      <select
        value={cycleId ?? ""}
        onChange={(e) => selectCycle(e.target.value)}
        aria-label="Switch campaign"
      >
        {!selectedKnown && cycleId && (
          <option value={cycleId} disabled>
            {cycleId} (not on disk)
          </option>
        )}
        {groupKeys.map((g) => (
          <optgroup key={g} label={g}>
            {groups.get(g)!.map((c) => (
              <option key={c.cycle_id} value={c.cycle_id}>
                {c.cycle_id === activeCycleId ? "● " : ""}
                {labelFor(c)}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </span>
  );
}

function labelFor(c: CycleListEntry): string {
  const id = c.cycle_id.length > 28 ? `${c.cycle_id.slice(0, 18)}…${c.cycle_id.slice(-6)}` : c.cycle_id;
  const best = c.best_accuracy == null ? "—" : `${(c.best_accuracy * 100).toFixed(0)}%`;
  return `${id} · ${c.sibling_kind} · best ${best} · ${c.status}`;
}
