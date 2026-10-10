"use client";
import { useRegistry } from "@/lib/registry";
import { useWorkspace } from "@/lib/workspace";
import { unitDisplayName } from "@/lib/names";
import { cx } from "@/lib/cx";
import { statusTone } from "@/lib/run-phase";
import type { CycleListEntry } from "@/lib/api";
import { Menu, MenuCheck } from "@/components/ui";
import { PotterMark } from "@/components/brand/PotterMark";

const POTTER_GLYPH = <PotterMark size={16} />;

export function JobsDock() {
  const { runningCycles, campaigns } = useRegistry();
  const { campaignId, cycleId, navigate } = useWorkspace();
  const n = runningCycles.length;

  if (n === 0) return null;

  const pick = (c: CycleListEntry) =>
    navigate([{ campaignId: c.campaign_id, cycleId: c.cycle_id }], { view: "dashboard" });

  const labelFor = (c: CycleListEntry) => {
    const campaign = campaigns.find((k) => k.campaign_id === c.campaign_id);
    const name = campaign ? campaign.display_name : c.campaign_id;
    return c.is_root ? name : `${name} · ${unitDisplayName(c)}`;
  };

  const c = n === 1 ? runningCycles[0] : undefined;
  if (c) {
    const label = labelFor(c);
    return (
      <div className="jobs-dock">
        <button
          type="button"
          className={cx("jobs-dock-btn", statusTone(c.status))}
          aria-label={`1 active job — ${label} (${c.status.label}). Go to it.`}
          title={`${c.status.label}: ${label}`}
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
                  <span className={cx("phase-chip", statusTone(r.status))}>
                    <span className="phase-dot" aria-hidden="true" />
                    {r.status.label}
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
