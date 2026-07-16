"use client";
import { SignInPrompt } from "@/components/ui";
import type { AuthStatus } from "@/lib/auth-context";
import type { LifecycleFilter } from "@/lib/api";
import type { OriginGroup } from "./sidebar/grouping";
import type { TreeCtx } from "./sidebar/ForestRows";
import { CampaignTreePane } from "./CampaignTreePane";
import { SidebarFilterPopover } from "./sidebar/SidebarFilterPopover";

interface Props {
  status: AuthStatus;
  loaded: boolean;
  lifecycleFilter: LifecycleFilter;
  setLifecycleFilter: (f: LifecycleFilter) => void;
  datasetNames: string[];
  datasetFilter: string | null;
  setDatasetFilter: (d: string | null) => void;
  origins: OriginGroup[];
  ctx: TreeCtx;
}

// The campaign-library body — the header + filter button, the auth/loading
// resting states, and the campaign forest. The lifecycle + dataset filters
// live behind the header's filter popover (SidebarFilterPopover) so the body
// stays a clean forest; when a non-default filter is set, one summary line
// keeps that fact visible with a one-click clear.
export function SidebarContent({
  status,
  loaded,
  lifecycleFilter,
  setLifecycleFilter,
  datasetNames,
  datasetFilter,
  setDatasetFilter,
  origins,
  ctx,
}: Props) {
  const filtered = lifecycleFilter === "archived" || datasetFilter != null;
  const clearFilters = () => {
    setLifecycleFilter("active");
    setDatasetFilter(null);
  };

  return (
    <div className="unit-library">
      <div className="unit-library-head">
        <span>Campaigns</span>
        <SidebarFilterPopover
          lifecycleFilter={lifecycleFilter}
          setLifecycleFilter={setLifecycleFilter}
          datasetNames={datasetNames}
          datasetFilter={datasetFilter}
          setDatasetFilter={setDatasetFilter}
        />
      </div>
      {filtered && (
        <div className="unit-library-active-filter">
          <span className="unit-library-active-filter-text">
            {lifecycleFilter === "archived" && <span>Archived</span>}
            {datasetFilter != null && <span>{datasetFilter}</span>}
          </span>
          <button
            type="button"
            className="unit-library-active-filter-clear"
            onClick={clearFilters}
            title="Clear filters"
            aria-label="Clear filters"
          >
            ✕
          </button>
        </div>
      )}
      {status !== "authed" ? (
        status === "loading" ? (
          <div className="unit-library-note">loading…</div>
        ) : (
          <SignInPrompt
            className="unit-library-note"
            message="Sign in to see your campaigns."
          />
        )
      ) : (
        !loaded && <div className="unit-library-note">loading…</div>
      )}
      {loaded && origins.length === 0 && lifecycleFilter === "archived" && (
        <div className="unit-library-empty">
          <div className="empty-headline">No archived campaigns</div>
          <div className="empty-body">
            Archive a campaign from its <code>⋯</code> menu to declutter the
            active list. Archives are reversible from this tab.
          </div>
        </div>
      )}
      {loaded && origins.length === 0 && lifecycleFilter !== "archived" && (
        <div className="unit-library-empty">
          {/* Points at the CTA already on screen (Sidebar's `+ New campaign`) rather than
              sending a browser user to a terminal and a repo path they can't open. */}
          <div className="empty-headline">No campaigns yet</div>
          <div className="empty-body">
            Hit <strong>+ New campaign</strong> above. Bring the data your pipeline gets
            wrong — the Potter reads it, sets up the first run, and starts improving.
          </div>
        </div>
      )}
      {loaded && origins.length > 0 && <CampaignTreePane origins={origins} ctx={ctx} />}
    </div>
  );
}
