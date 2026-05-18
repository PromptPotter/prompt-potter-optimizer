"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchCycles, type CycleListEntry, type SiblingKind } from "@/lib/api";
import { shortFamilyTail } from "@/lib/ids";
import { TERMS } from "@/lib/terms";

interface Props {
  cycleId: string | null;
  onSelectCycle: (id: string) => void;
  onNewCycle: () => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

// One family = the root cycle + every sibling that derives from it
// (forks, sweeps, diag). Disk already nests this way under
// campaigns/{root}/{forks,sweeps,diag}/...; the flat /cycles response just
// hadn't been re-grouped on the client. `parent_cycle_id` (new on the API)
// is the join key.
interface Family {
  root: CycleListEntry;
  forks: CycleListEntry[];
  sweeps: CycleListEntry[];
  diag: CycleListEntry[];
  // Most-recent updated_at across the whole family — sorts families so the
  // one being actively worked on stays at the top regardless of how old the
  // root's index.json is.
  updatedAt: string;
}

const SIBLING_GROUPS: { kind: SiblingKind; label: string; pluralBadge: string }[] = [
  // diag is rare in practice; ordering keeps the common kinds first.
  { kind: "fork", label: "Forks", pluralBadge: "⑂" },
  { kind: "sweep", label: "Sweeps", pluralBadge: "~" },
  { kind: "diag", label: "Diag", pluralBadge: "Δ" },
];

function groupByFamily(cycles: CycleListEntry[]): Family[] {
  // Stub roots: a sibling whose root id never appears as its own entry
  // (e.g. the operator deleted the root dir but kept the forks). Without
  // synthesising a placeholder root we'd silently drop those siblings from
  // the sidebar entirely.
  const roots = new Map<string, CycleListEntry>();
  for (const c of cycles) {
    if (c.is_root) roots.set(c.cycle_id, c);
  }
  for (const c of cycles) {
    if (!c.is_root && c.parent_cycle_id && !roots.has(c.parent_cycle_id)) {
      roots.set(c.parent_cycle_id, {
        cycle_id: c.parent_cycle_id,
        parent_session_id: "",
        parent_cycle_id: null,
        dataset_name: c.dataset_name,
        backend_id: c.backend_id,
        sibling_kind: "root",
        is_root: true,
        status: "missing",
        best_accuracy: null,
        n_rounds: 0,
        created_at: "",
        updated_at: c.updated_at,
      });
    }
  }
  const families = new Map<string, Family>();
  for (const root of roots.values()) {
    families.set(root.cycle_id, {
      root,
      forks: [],
      sweeps: [],
      diag: [],
      updatedAt: root.updated_at,
    });
  }
  for (const c of cycles) {
    if (c.is_root) continue;
    const parentId = c.parent_cycle_id;
    if (!parentId) continue;
    const fam = families.get(parentId);
    if (!fam) continue;
    if (c.sibling_kind === "fork") fam.forks.push(c);
    else if (c.sibling_kind === "sweep") fam.sweeps.push(c);
    else if (c.sibling_kind === "diag") fam.diag.push(c);
    if (c.updated_at > fam.updatedAt) fam.updatedAt = c.updated_at;
  }
  for (const fam of families.values()) {
    const byUpdated = (a: CycleListEntry, b: CycleListEntry) =>
      a.updated_at < b.updated_at ? 1 : -1;
    fam.forks.sort(byUpdated);
    fam.sweeps.sort(byUpdated);
    fam.diag.sort(byUpdated);
  }
  return [...families.values()].sort((a, b) =>
    a.updatedAt < b.updatedAt ? 1 : -1,
  );
}

function fmtAcc(v: number | null): string {
  return v == null ? "—" : `${(v * 100).toFixed(0)}%`;
}

// We store the COLLAPSED set, not the expanded one — families default to
// expanded when they have children, so empty storage = "show everything."
// Only families the operator has explicitly collapsed get persisted.
const COLLAPSED_STORAGE_KEY = "promptpotter.sidebar.collapsedFamilies";

function loadCollapsed(): Set<string> {
  try {
    const raw = window.localStorage.getItem(COLLAPSED_STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return new Set(Array.isArray(parsed) ? parsed : []);
  } catch {
    return new Set();
  }
}

function saveCollapsed(s: Set<string>) {
  try {
    window.localStorage.setItem(COLLAPSED_STORAGE_KEY, JSON.stringify([...s]));
  } catch {
    /* private mode etc. */
  }
}

export function Sidebar({ cycleId, onSelectCycle, onNewCycle, collapsed, onToggleCollapse }: Props) {
  const [cycles, setCycles] = useState<CycleListEntry[] | null>(null);
  const [activeCycleId, setActiveCycleId] = useState<string | null>(null);
  // Bumped manually and on `window` focus to pick up cycles the CLI minted
  // in another terminal. No periodic poll — the list changes rarely.
  const [tick, setTick] = useState(0);
  // Family-collapse state — families with children expand by default
  // (operators want to see what's there), so we persist the families the
  // operator has explicitly collapsed instead of the ones they've expanded.
  // Empty set on first visit ⇒ everything is visible. Named
  // `collapsedFamilies` to avoid colliding with the `collapsed` prop that
  // tracks the whole sidebar's collapse state.
  const [collapsedFamilies, setCollapsedFamilies] = useState<Set<string>>(() => new Set());

  // Hydrate stored collapsed set after mount (localStorage is browser-only).
  useEffect(() => {
    setCollapsedFamilies(loadCollapsed());
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetchCycles();
        if (cancelled) return;
        setCycles(r.cycles);
        setActiveCycleId(r.active_cycle_id);
      } catch {
        // silent — sidebar degrades to nav-only when /cycles is unreachable
      }
    })();
    const onFocus = () => setTick((t) => t + 1);
    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", onFocus);
    };
  }, [tick]);

  const families = useMemo(() => groupByFamily(cycles ?? []), [cycles]);

  // Auto-expand the family that contains the active/selected cycle. Map
  // either cycleId or activeCycleId to its family-root and force-expand —
  // operators expect "where am I?" to be visible without a click. We never
  // auto-collapse; explicit collapse beats helpfulness here.
  const focusRoot = useMemo(() => {
    const id = cycleId ?? activeCycleId;
    if (!id) return null;
    const direct = families.find((f) => f.root.cycle_id === id);
    if (direct) return direct.root.cycle_id;
    const child = families.find((f) =>
      [...f.forks, ...f.sweeps, ...f.diag].some((c) => c.cycle_id === id),
    );
    return child?.root.cycle_id ?? null;
  }, [families, cycleId, activeCycleId]);

  // The family containing the active/selected cycle is force-expanded —
  // if the operator collapsed it earlier we still uncollapse it here, so
  // "where am I?" is always visible without a chevron click.
  useEffect(() => {
    if (!focusRoot) return;
    setCollapsedFamilies((prev) => {
      if (!prev.has(focusRoot)) return prev;
      const next = new Set(prev);
      next.delete(focusRoot);
      saveCollapsed(next);
      return next;
    });
  }, [focusRoot]);

  const toggleFamily = useCallback((rootId: string) => {
    setCollapsedFamilies((prev) => {
      const next = new Set(prev);
      if (next.has(rootId)) next.delete(rootId);
      else next.add(rootId);
      saveCollapsed(next);
      return next;
    });
  }, []);

  // Family rows that have no children behave like a normal leaf cycle:
  // clicking selects the root. Family rows that DO have children also
  // expand on a label click when collapsed — that way the operator can
  // never end up with a row they can't drill into. Re-clicking does not
  // collapse (that's what the chevron is for).
  const selectFamily = useCallback(
    (fam: Family) => {
      onSelectCycle(fam.root.cycle_id);
      const hasChildren = fam.forks.length + fam.sweeps.length + fam.diag.length > 0;
      if (!hasChildren) return;
      setCollapsedFamilies((prev) => {
        if (!prev.has(fam.root.cycle_id)) return prev;
        const next = new Set(prev);
        next.delete(fam.root.cycle_id);
        saveCollapsed(next);
        return next;
      });
    },
    [onSelectCycle],
  );

  return (
    <nav className="sidebar" aria-label="Primary">
      <button
        type="button"
        className="sidebar-toggle"
        onClick={onToggleCollapse}
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-expanded={!collapsed}
      >
        {collapsed ? "›" : "‹"}
      </button>
      <div className="brand">
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 2 }}>
          <div style={{ width: 22, height: 22, background: "var(--color-accent)", borderRadius: 4, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
              <rect x="1" y="1" width="4" height="4" rx="1" fill="white" />
              <rect x="7" y="1" width="4" height="4" rx="1" fill="white" opacity=".7" />
              <rect x="1" y="7" width="4" height="4" rx="1" fill="white" opacity=".7" />
              <rect x="7" y="7" width="4" height="4" rx="1" fill="white" opacity=".4" />
            </svg>
          </div>
          <span className="brand-name">PromptPotter</span>
        </div>
        <div className="brand-sub" title={TERMS.brand_live_preview}>LIVE PREVIEW</div>
      </div>
      <div className="sidebar-primary">
        <button
          type="button"
          className="sidebar-cta"
          onClick={onNewCycle}
          title="Start a new campaign"
        >
          + New campaign
        </button>
      </div>
      <div className="cycle-library">
        <div className="cycle-library-head">
          <span>Campaigns</span>
          <button
            type="button"
            className="cycle-library-refresh"
            onClick={() => setTick((t) => t + 1)}
            title="Refresh campaign list"
            aria-label="Refresh campaign list"
          >
            ↻
          </button>
        </div>
        {cycles === null && <div className="cycle-library-note">loading…</div>}
        {cycles !== null && families.length === 0 && (
          <div className="cycle-library-note">
            None on disk yet — run <code>python -m promptpotter new &lt;dataset&gt;</code>.
          </div>
        )}
        {cycles !== null && families.length > 0 && (
          <ul className="cycle-library-list">
            {families.map((fam) => {
              const isOpen = !collapsedFamilies.has(fam.root.cycle_id);
              const totalChildren = fam.forks.length + fam.sweeps.length + fam.diag.length;
              return (
                <li key={fam.root.cycle_id}>
                  <FamilyRow
                    family={fam}
                    isOpen={isOpen}
                    selected={fam.root.cycle_id === cycleId}
                    active={fam.root.cycle_id === activeCycleId}
                    onToggle={() => toggleFamily(fam.root.cycle_id)}
                    onSelect={() => selectFamily(fam)}
                  />
                  {isOpen && totalChildren > 0 && (
                    <ul className="cycle-library-children">
                      {SIBLING_GROUPS.map(({ kind, label }) => {
                        const items =
                          kind === "fork" ? fam.forks : kind === "sweep" ? fam.sweeps : fam.diag;
                        if (items.length === 0) return null;
                        return (
                          <li key={kind} className="cycle-library-group">
                            <div className="cycle-library-grouphead">
                              {label} · {items.length}
                            </div>
                            <ul>
                              {items.map((c) => (
                                <li key={c.cycle_id}>
                                  <ChildRow
                                    cycle={c}
                                    selected={c.cycle_id === cycleId}
                                    active={c.cycle_id === activeCycleId}
                                    onSelect={() => onSelectCycle(c.cycle_id)}
                                  />
                                </li>
                              ))}
                            </ul>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <div className="sidebar-footer">
        <div className="sidebar-footer-item">Support</div>
        <div className="sidebar-footer-item">Log out</div>
      </div>
    </nav>
  );
}

function FamilyRow({
  family,
  isOpen,
  selected,
  active,
  onToggle,
  onSelect,
}: {
  family: Family;
  isOpen: boolean;
  selected: boolean;
  active: boolean;
  onToggle: () => void;
  onSelect: () => void;
}) {
  const { root, forks, sweeps, diag } = family;
  // Status badge — running/optimizing is fresher info than index.json best
  // and the operator wants it visible at the family-row level.
  const live = root.status === "running" || root.status === "optimizing";
  // Tiny clickable chips for each child kind. Each chip is one square with
  // a glyph + count; clicking a chip = toggle the family (same as the
  // chevron). Glyph: ⑂ = fork, ~ = sweep, Δ = diag. The tooltip carries
  // the plain-English meaning so the glyph isn't load-bearing.
  const chips: { kind: string; glyph: string; count: number; title: string }[] = [
    { kind: "fork", glyph: "⑂", count: forks.length, title: `${forks.length} fork${forks.length === 1 ? "" : "s"}` },
    { kind: "sweep", glyph: "~", count: sweeps.length, title: `${sweeps.length} sweep${sweeps.length === 1 ? "" : "s"}` },
    { kind: "diag", glyph: "Δ", count: diag.length, title: `${diag.length} diag` },
  ].filter((c) => c.count > 0);
  return (
    <div className={`cycle-library-family${selected ? " selected" : ""}`}>
      <button
        type="button"
        className="cycle-library-twist"
        onClick={onToggle}
        aria-label={isOpen ? "Collapse family" : "Expand family"}
        aria-expanded={isOpen}
        // Disabled when there's nothing to expand — keeps the chevron column
        // aligned across rows but doesn't fire a no-op toggle.
        disabled={forks.length + sweeps.length + diag.length === 0}
        tabIndex={-1}
      >
        {forks.length + sweeps.length + diag.length === 0 ? "" : isOpen ? "▼" : "▶"}
      </button>
      <button
        type="button"
        className="cycle-library-item"
        onClick={onSelect}
        aria-current={selected ? "true" : undefined}
        title={root.cycle_id}
      >
        <span className="cycle-library-mark">{active ? "●" : ""}</span>
        <span className="cycle-library-row">
          <span className="cycle-library-name">
            {root.dataset_name || root.cycle_id}
            {live && <span className="cycle-library-live" title="Campaign status is running">●</span>}
            {root.status === "missing" && (
              <span className="cycle-library-missing" title="Root dir not on disk; only sibling artifacts present">
                ⚠
              </span>
            )}
          </span>
          <span className="cycle-library-meta">{fmtAcc(root.best_accuracy)}</span>
        </span>
      </button>
      {/* Chips sit OUTSIDE the .cycle-library-item button — nested buttons are
          invalid HTML and React warns. The chips' own onClick handles
          family-toggle; they never bubble into the row-select handler. */}
      {chips.length > 0 && (
        <span className="cycle-library-chips" aria-hidden={false}>
          {chips.map((chip) => (
            <button
              key={chip.kind}
              type="button"
              className="cycle-library-chip"
              onClick={onToggle}
              title={chip.title}
              aria-label={chip.title}
              tabIndex={-1}
            >
              <span className="cycle-library-chip-glyph" aria-hidden="true">{chip.glyph}</span>
              <span className="cycle-library-chip-count">{chip.count}</span>
            </button>
          ))}
        </span>
      )}
    </div>
  );
}

function ChildRow({
  cycle,
  selected,
  active,
  onSelect,
}: {
  cycle: CycleListEntry;
  selected: boolean;
  active: boolean;
  onSelect: () => void;
}) {
  const live = cycle.status === "running" || cycle.status === "optimizing";
  // Child rows show the disambiguating suffix only — the family-root prefix
  // is already in the parent row's title and would just eat horizontal
  // space here. Falls back to full id when the parse fails (defensive only).
  const shortLabel = shortFamilyTail(cycle.cycle_id);
  return (
    <button
      type="button"
      className={`cycle-library-item cycle-library-child${selected ? " selected" : ""}`}
      onClick={onSelect}
      aria-current={selected ? "true" : undefined}
      title={cycle.cycle_id}
    >
      <span className="cycle-library-mark">{active ? "●" : ""}</span>
      <span className="cycle-library-row">
        <span className="cycle-library-name">
          {shortLabel}
          {live && <span className="cycle-library-live" title="Campaign status is running">●</span>}
        </span>
        <span className="cycle-library-meta">{fmtAcc(cycle.best_accuracy)}</span>
      </span>
    </button>
  );
}

