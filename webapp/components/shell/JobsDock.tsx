"use client";
import { useRegistry } from "@/lib/registry";
import { useWorkspace } from "@/lib/workspace";
import { unitDisplayName } from "@/lib/names";
import { cx } from "@/lib/cx";
import { fmtAgo } from "@/lib/format";
import { STATUS_MARK, statusTone } from "@/lib/run-phase";
import { readyData } from "@/lib/hooks/useRead";
import { useMachineStatus } from "@/lib/hooks/useMachineStatus";
import type { CycleListEntry, MachineSlot } from "@/lib/api";
import { Menu, MenuCheck, MenuItem } from "@/components/ui";
import { PotterMark } from "@/components/brand/PotterMark";

const POTTER_GLYPH = <PotterMark size={16} />;

// A pass has no cycle, so no status: its phase is whether it has started.
function passMark(s: MachineSlot): "running" | "starting" {
  return s.started_at === null ? "starting" : "running";
}

function passLabel(s: MachineSlot): string {
  return `${s.what} · ${s.started_at === null ? "starting" : `started ${fmtAgo(s.started_at)}`}`;
}

export function JobsDock() {
  const { runningCycles, campaigns } = useRegistry();
  const { campaignId, cycleId, navigate, openAccount } = useWorkspace();
  const machine = readyData(useMachineStatus());
  // A run's slot is its cycle, already listed above; only a pass is the machine read's alone.
  const passes = (machine?.running ?? []).filter(
    (s) => s.mine && s.holder.kind === "scoring_pass",
  );
  const n = runningCycles.length + passes.length;

  if (n === 0) return null;

  const pick = (c: CycleListEntry) =>
    navigate([{ campaignId: c.campaign_id, cycleId: c.cycle_id }], { view: "dashboard" });
  const openUsage = () => openAccount("usage");

  const labelFor = (c: CycleListEntry) => {
    const campaign = campaigns.find((k) => k.campaign_id === c.campaign_id);
    const name = campaign ? campaign.display_name : c.campaign_id;
    return c.is_root ? name : `${name} · ${unitDisplayName(c)}`;
  };

  if (n === 1) {
    const c = runningCycles[0];
    const p = passes[0];
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
    if (p) {
      return (
        <div className="jobs-dock">
          <button
            type="button"
            className="jobs-dock-btn"
            aria-label={`1 active job — ${passLabel(p)}. A scoring pass has no run to open; open account usage.`}
            title={passLabel(p)}
            onClick={openUsage}
          >
            {POTTER_GLYPH}
          </button>
        </div>
      );
    }
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
            {passes.map((s) => (
              <MenuItem
                key={s.job_id}
                onClick={() => {
                  openUsage();
                  close();
                }}
              >
                <span className="jobs-dock-row">
                  <span className={cx("phase-chip", `tone-${STATUS_MARK[passMark(s)].tone}`)}>
                    <span className="phase-dot" aria-hidden="true" />
                    {passMark(s)}
                  </span>
                  <span className="jobs-dock-item-label">{passLabel(s)}</span>
                </span>
              </MenuItem>
            ))}
          </>
        )}
      </Menu>
    </div>
  );
}
