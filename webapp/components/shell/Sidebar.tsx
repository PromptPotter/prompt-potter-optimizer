"use client";
import { useEffect, useState } from "react";
import { fetchCycles, type CycleListEntry } from "@/lib/api";
import { TERMS } from "@/lib/terms";

interface Props {
  // Persistent across all top-tab views (Chat / Dashboard / Files) —
  // Replit-style navigation. The pane-level sub-views moved into the
  // Topbar; the sidebar is now scoped to cycle navigation: a primary
  // "New cycle" action and the cycle library beneath it. Currently-
  // viewed cycle is highlighted; the active cycle (per
  // active_session.json) is bullet-marked.
  cycleId: string | null;
  onSelectCycle: (id: string) => void;
  onNewCycle: () => void;
}

export function Sidebar({ cycleId, onSelectCycle, onNewCycle }: Props) {
  const [cycles, setCycles] = useState<CycleListEntry[] | null>(null);
  const [activeCycleId, setActiveCycleId] = useState<string | null>(null);
  // Bumped manually and on `window` focus to pick up cycles the CLI minted
  // in another terminal. No periodic poll — the list changes rarely.
  const [tick, setTick] = useState(0);

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

  return (
    <nav className="sidebar" aria-label="Primary">
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
          title="Start a new cycle"
        >
          + New cycle
        </button>
      </div>
      <div className="cycle-library">
        <div className="cycle-library-head">
          <span>Cycles</span>
          <button
            type="button"
            className="cycle-library-refresh"
            onClick={() => setTick((t) => t + 1)}
            title="Refresh cycle list"
            aria-label="Refresh cycle list"
          >
            ↻
          </button>
        </div>
        {cycles === null && <div className="cycle-library-note">loading…</div>}
        {cycles !== null && cycles.length === 0 && (
          <div className="cycle-library-note">
            None on disk yet — run <code>python -m promptpotter optimize --config datasets/…/campaign.json</code>.
          </div>
        )}
        {cycles !== null && cycles.length > 0 && (
          <ul className="cycle-library-list">
            {[...cycles]
              .sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1))
              .map((c) => {
                const selected = c.cycle_id === cycleId;
                const active = c.cycle_id === activeCycleId;
                // index.json status — "running"/"optimizing" while a
                // cycle is being driven by the CLI; anything else is
                // frozen. Best-effort; the breadcrumb's `● Live` uses
                // the dashboard.json mtime heuristic which is fresher.
                const live = c.status === "running" || c.status === "optimizing";
                return (
                  <li key={c.cycle_id}>
                    <button
                      type="button"
                      className={`cycle-library-item${selected ? " selected" : ""}`}
                      onClick={() => onSelectCycle(c.cycle_id)}
                      aria-current={selected ? "true" : undefined}
                      title={c.cycle_id}
                    >
                      <span className="cycle-library-mark">{active ? "●" : ""}</span>
                      <span className="cycle-library-row">
                        <span className="cycle-library-name">
                          {c.dataset_name || c.cycle_id}
                          {live && <span className="cycle-library-live" title="Cycle status is running">●</span>}
                        </span>
                        <span className="cycle-library-meta">
                          {c.sibling_kind === "root" ? "" : `${c.sibling_kind} · `}
                          {c.best_accuracy == null ? "—" : `${(c.best_accuracy * 100).toFixed(0)}%`}
                        </span>
                      </span>
                    </button>
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
