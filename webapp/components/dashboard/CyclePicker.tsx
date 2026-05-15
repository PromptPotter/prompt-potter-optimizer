"use client";
import { useEffect, useState } from "react";
import { fetchCycles, type CycleListEntry } from "@/lib/api";

interface Props {
  cycleId: string | null;
  onChange: (id: string) => void;
}

// Inline picker. The breadcrumb text becomes a `<select>` styled to read
// as text — the operator clicks the cycle id and gets a native dropdown
// grouped by dataset_name. Native control = free keyboard nav + click-
// outside-to-close + a11y. Refresh button next to it picks up new cycles
// minted by the CLI mid-session.
export function CyclePicker({ cycleId, onChange }: Props) {
  const [cycles, setCycles] = useState<CycleListEntry[] | null>(null);
  const [activeCycleId, setActiveCycleId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Bumped to trigger a refetch — used by the manual refresh button and the
  // window-focus listener. Keeping the fetch inside the effect body (rather
  // than a useCallback) avoids the eslint react-hooks/set-state-in-effect
  // false positive that fires when an effect calls a memoized setState'd fn.
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetchCycles();
        if (cancelled) return;
        setCycles(r.cycles);
        setActiveCycleId(r.active_cycle_id);
        setErr(null);
      } catch (e) {
        if (!cancelled) setErr((e as Error).message);
      }
    })();
    const onFocus = () => setRefreshTick((t) => t + 1);
    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", onFocus);
    };
  }, [refreshTick]);

  if (err) return <span className="cycle-picker-err">campaigns: {err}</span>;
  if (!cycles || cycles.length === 0) {
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
        onChange={(e) => onChange(e.target.value)}
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
      <button
        type="button"
        className="cycle-picker-refresh"
        onClick={() => setRefreshTick((t) => t + 1)}
        title="Refresh campaign list"
        aria-label="Refresh campaign list"
      >
        ↻
      </button>
    </span>
  );
}

function labelFor(c: CycleListEntry): string {
  const id = c.cycle_id.length > 28 ? `${c.cycle_id.slice(0, 18)}…${c.cycle_id.slice(-6)}` : c.cycle_id;
  const best = c.best_accuracy == null ? "—" : `${(c.best_accuracy * 100).toFixed(0)}%`;
  return `${id} · ${c.sibling_kind} · best ${best} · ${c.status}`;
}
