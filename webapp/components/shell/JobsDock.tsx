"use client";
import { useWorkspace } from "@/lib/workspace";
import { runPhaseLabel } from "@/lib/run-phase";
import { campaignDisplayName, unitDisplayName } from "@/lib/names";
import { cx } from "@/lib/cx";
import type { CycleListEntry } from "@/lib/api";
import { Menu, MenuCheck } from "@/components/ui";
import { PotterMark } from "@/components/brand/PotterMark";

// The live-run dock on the sidebar's outer edge; absent when idle — the absence IS "all quiet",
// which is why it lists PRODUCERS only (never `paused` or `detached`).

interface Props {
  onPicked?: () => void;
}

const POTTER_GLYPH = <PotterMark size={16} />;

export function JobsDock({ onPicked }: Props) {
  const { runningCycles, campaigns, campaignId, cycleId, selectCycle } = useWorkspace();
  const n = runningCycles.length;

  if (n === 0) return null;

  const pick = (c: CycleListEntry) => {
    selectCycle(c.campaign_id, c.cycle_id);
    onPicked?.();
  };

  // A fork runs under its campaign's name, so two live cycles of one campaign need the fork's too.
  const labelFor = (c: CycleListEntry) => {
    const campaign = campaigns.find((k) => k.campaign_id === c.campaign_id);
    const name = campaign ? campaignDisplayName(campaign) : c.campaign_id;
    return c.is_root ? name : `${name} · ${unitDisplayName(c)}`;
  };

  // Still carries its phase class: a run held at the origin gate must not look like one
  // making progress.
  const c = n === 1 ? runningCycles[0] : undefined;
  if (c) {
    const label = labelFor(c);
    return (
      <div className="jobs-dock">
        <button
          type="button"
          className={cx("jobs-dock-btn", `phase-${c.run_phase}`)}
          aria-label={`1 active job — ${label} (${runPhaseLabel(c.run_phase, c.status)}). Go to it.`}
          title={`${runPhaseLabel(c.run_phase, c.status)}: ${label}`}
          onClick={() => pick(c)}
        >
          {POTTER_GLYPH}
        </button>
      </div>
    );
  }

  return (
    <div className="jobs-dock">
      <Menu
        align="left"
        renderTrigger={({ open, toggle }) => (
          <button
            type="button"
            className="jobs-dock-btn"
            aria-label={`${n} active jobs. Open the list.`}
            aria-expanded={open}
            aria-haspopup="menu"
            title={`${n} active jobs`}
            onClick={toggle}
          >
            {POTTER_GLYPH}
            <span className="jobs-dock-count" aria-hidden="true">
              {n}
            </span>
          </button>
        )}
      >
        {({ close }) => (
          <>
            {/* Already in the shared `dockPriority` order — never re-sorted here. The tick is the
                run on screen, so the list says where a switch starts from. */}
            {runningCycles.map((r) => (
              <MenuCheck
                key={`${r.campaign_id}/${r.cycle_id}`}
                on={r.campaign_id === campaignId && r.cycle_id === cycleId}
                onClick={() => {
                  pick(r);
                  close();
                }}
              >
                <span className="jobs-dock-row">
                  <span className={cx("phase-chip", `phase-${r.run_phase}`)}>
                    <span className="phase-dot" aria-hidden="true" />
                    {runPhaseLabel(r.run_phase, r.status)}
                  </span>
                  <span className="jobs-dock-item-label">{labelFor(r)}</span>
                </span>
              </MenuCheck>
            ))}
          </>
        )}
      </Menu>
    </div>
  );
}
