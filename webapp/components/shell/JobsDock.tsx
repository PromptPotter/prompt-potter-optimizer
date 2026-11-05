import { useRegistry } from "@/lib/hooks/useRegistry";
import { useWorkspace } from "@/lib/workspace";
import { cx } from "@/lib/cx";
import { decodeCyclePath, encodeCyclePath } from "@/lib/ids";
import { fmtAgo } from "@/lib/format";
import { STATUS_MARK_INFO, type HierarchyCourse, type MachineSlot } from "@/lib/api/types";
import { figureOf } from "@/lib/derivations";
import { readyData } from "@/lib/hooks/useRead";
import { useMachineStatus } from "@/lib/hooks/useMachineStatus";
import { Menu, MenuCheck, MenuItem } from "@/components/ui";
import { PotterMark } from "@/components/brand/PotterMark";

const POTTER_GLYPH = <PotterMark size={16} />;

// A pass scoring no cycle has no status: its phase is whether it has started.
function passMark(s: MachineSlot): "running" | "starting" {
  return s.started_at === null ? "starting" : "running";
}

function passLabel(s: MachineSlot): string {
  return `${s.what} · ${s.started_at === null ? "starting" : `started ${fmtAgo(s.started_at)}`}`;
}

export function JobsDock() {
  const { running, byAddress } = useRegistry();
  const { campaignId, cycleId, navigate, open } = useWorkspace();
  const viewed = campaignId && cycleId ? encodeCyclePath([{ campaignId, cycleId }]) : null;
  const machine = readyData(useMachineStatus());
  // A slot holding a cycle is that cycle, already listed above; only a cycleless pass is the
  // machine read's alone.
  const passes = (machine?.running ?? []).filter(
    (s) => s.mine && s.holder.kind === "scoring_pass" && s.cycle === null,
  );
  const n = running.length + passes.length;

  if (n === 0) return null;

  const pick = (c: HierarchyCourse) => {
    const path = decodeCyclePath(c.address);
    if (path) navigate({ path });
  };
  const openUsage = () => open("account", "usage");

  // Served words only: the campaign the job belongs to, then which course of it — none for its root.
  const wordsOf = (c: HierarchyCourse): [string, string | null] => {
    const campaign = c.parent === null ? undefined : byAddress.get(c.parent);
    if (campaign?.level !== "campaign") return [c.label, null];
    return [campaign.label, campaign.facts.root === c.address ? null : c.label];
  };

  if (n === 1) {
    const c = running[0];
    const p = passes[0];
    if (c) {
      const label = wordsOf(c)
        .filter((w) => w !== null)
        .join(", ");
      const state = figureOf(c, "state");
      return (
        <div className="jobs-dock">
          <button
            type="button"
            className={cx("jobs-dock-btn", state && `tone-${state.tone}`)}
            aria-label={`1 active job — ${label} (${state?.value}). Go to it.`}
            title={`${state?.value}: ${label}`}
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
            {running.map((r) => (
              <MenuCheck
                key={r.address}
                on={r.address === viewed}
                onClick={() => {
                  pick(r);
                  close();
                }}
              >
                <span className="jobs-dock-row">
                  <StateChip course={r} />
                  <span className="jobs-dock-item-label">{wordsOf(r)[0]}</span>
                  {wordsOf(r)[1] !== null && (
                    <span className="jobs-dock-item-course">{wordsOf(r)[1]}</span>
                  )}
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
                  <span className={cx("phase-chip", `tone-${STATUS_MARK_INFO[passMark(s)].tone}`)}>
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

function StateChip({ course }: { course: HierarchyCourse }) {
  const state = figureOf(course, "state");
  return (
    <span className={cx("phase-chip", state && `tone-${state.tone}`)}>
      <span className="phase-dot" aria-hidden="true" />
      {state?.value}
    </span>
  );
}
