"use client";
import { SignInPrompt } from "@/components/ui";
import type { AuthStatus } from "@/lib/auth-context";
import type { LifecycleFilter } from "@/lib/api";
import type { CampaignGroup } from "./sidebar/grouping";
import { CampaignTreePane } from "./CampaignTreePane";

interface Props {
  status: AuthStatus;
  loaded: boolean;
  lifecycleFilter: LifecycleFilter;
  setLifecycleFilter: (f: LifecycleFilter) => void;
  datasetNames: string[];
  datasetFilter: string | null;
  setDatasetFilter: (d: string | null) => void;
  groups: CampaignGroup[];
  collapsedNodes: Set<string>;
  toggleNode: (key: string) => void;
  campaignId: string | null;
  cycleId: string | null;
  activeCampaignId: string | null;
  activeCycleId: string | null;
  onSelectCycle: (campaignId: string, cycleId: string) => void;
}

// The campaign library body — lifecycle tabs, dataset filter, the auth/loading
// resting states, and the campaign forest. Extracted from Sidebar, which stays
// the shell wrapper (toggle, brand, footer) and owns the workspace + auth state
// + collapse + dataset-filter handlers this renders.
export function SidebarContent({
  status,
  loaded,
  lifecycleFilter,
  setLifecycleFilter,
  datasetNames,
  datasetFilter,
  setDatasetFilter,
  groups,
  collapsedNodes,
  toggleNode,
  campaignId,
  cycleId,
  activeCampaignId,
  activeCycleId,
  onSelectCycle,
}: Props) {
  return (
    <div className="unit-library">
      <div className="unit-library-head">
        <span>Campaigns</span>
      </div>
      <div className="unit-library-tabs" role="tablist" aria-label="Campaign lifecycle">
        <button
          type="button"
          role="tab"
          className={`unit-library-tab${lifecycleFilter === "active" ? " active" : ""}`}
          onClick={() => setLifecycleFilter("active")}
          aria-selected={lifecycleFilter === "active"}
        >
          Active
        </button>
        <button
          type="button"
          role="tab"
          className={`unit-library-tab${lifecycleFilter === "archived" ? " active" : ""}`}
          onClick={() => setLifecycleFilter("archived")}
          aria-selected={lifecycleFilter === "archived"}
          title="Show archived campaigns. Deleted campaigns are hidden — read them by id from the file tree."
        >
          Archived
        </button>
      </div>
      {datasetNames.length > 1 && (
        <DatasetFilterBar
          datasets={datasetNames}
          selected={datasetFilter}
          onSelect={setDatasetFilter}
        />
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
      {loaded && groups.length === 0 && lifecycleFilter === "archived" && (
        <div className="unit-library-empty">
          <div className="empty-headline">No archived campaigns</div>
          <div className="empty-body">
            Archive a campaign from its <code>⋯</code> menu to declutter the
            active list. Archives are reversible from this tab.
          </div>
        </div>
      )}
      {loaded && groups.length === 0 && lifecycleFilter !== "archived" && (
        <div className="unit-library-empty">
          <div className="empty-headline">No campaigns yet</div>
          <div className="empty-body">
            Start your first campaign from a terminal:
          </div>
          <pre className="empty-cmd"><code>python -m promptpotter new &lt;dataset&gt;</code></pre>
          <div className="empty-hint">
            See <code>docs/manual/</code> for the quickstart.
          </div>
        </div>
      )}
      {loaded && groups.length > 0 && (
        <CampaignTreePane
          groups={groups}
          collapsedNodes={collapsedNodes}
          toggleNode={toggleNode}
          campaignId={campaignId}
          cycleId={cycleId}
          activeCampaignId={activeCampaignId}
          activeCycleId={activeCycleId}
          onSelectCycle={onSelectCycle}
        />
      )}
    </div>
  );
}

// Dataset filter chip-bar — narrows the flat campaign list to one dataset.
function DatasetFilterBar({
  datasets,
  selected,
  onSelect,
}: {
  datasets: string[];
  selected: string | null;
  onSelect: (d: string | null) => void;
}) {
  return (
    <div className="unit-library-filter" role="group" aria-label="Filter by dataset">
      <button
        type="button"
        className={`unit-library-filter-chip${selected == null ? " active" : ""}`}
        onClick={() => onSelect(null)}
      >
        All
      </button>
      {datasets.map((d) => (
        <button
          key={d}
          type="button"
          className={`unit-library-filter-chip${selected === d ? " active" : ""}`}
          onClick={() => onSelect(selected === d ? null : d)}
          title={d}
        >
          {d}
        </button>
      ))}
    </div>
  );
}
