import { useMemo } from "react";
import { Empty, FailureLine, Loading, SignInPrompt } from "@/components/ui";
import { useAuth } from "@/lib/state/auth";
import { failureOf, shownData } from "@/lib/hooks/useRead";
import { useRegistry } from "@/lib/hooks/useRegistry";
import { ForestRows } from "./ForestRows";
import {
  clearListedFilter,
  datasetOf,
  isFiltered,
  listedUnder,
  useListedFilter,
} from "./listed-filter";
import { SidebarFilterPopover } from "./SidebarFilterPopover";

export function SidebarContent() {
  const { status } = useAuth();
  const { read, entries } = useRegistry();
  const filter = useListedFilter();
  const loaded = shownData(read) !== null;
  const failure = failureOf(read);

  const listed = useMemo(
    () => entries.filter((c) => listedUnder(filter.lifecycle, c)),
    [entries, filter.lifecycle],
  );
  const shown = useMemo(
    () => (filter.dataset == null ? listed : listed.filter((c) => datasetOf(c) === filter.dataset)),
    [listed, filter.dataset],
  );
  const datasetNames = useMemo(() => [...new Set(listed.map(datasetOf))].sort(), [listed]);

  return (
    <div className="sidebar-tree">
      <div className="sidebar-tree-head">
        <span>Campaigns</span>
        <SidebarFilterPopover datasetNames={datasetNames} />
      </div>
      {isFiltered(filter) && (
        <div className="sidebar-tree-active-filter">
          <span className="sidebar-tree-active-filter-text">
            {filter.lifecycle === "archived" && <span>Archived</span>}
            {filter.dataset != null && <span>{filter.dataset}</span>}
          </span>
          <button
            type="button"
            className="sidebar-tree-active-filter-clear"
            onClick={clearListedFilter}
            title="Clear filters"
            aria-label="Clear filters"
          >
            ✕
          </button>
        </div>
      )}
      {status !== "authed" ? (
        status === "loading" ? (
          <Loading className="sidebar-tree-note">loading…</Loading>
        ) : (
          <SignInPrompt
            className="sidebar-tree-note"
            message="Sign in to see your campaigns."
          />
        )
      ) : (
        !loaded &&
        (failure !== null ? (
          <FailureLine failure={failure} className="sidebar-tree-note" />
        ) : (
          <Loading className="sidebar-tree-note">loading…</Loading>
        ))
      )}
      {loaded && shown.length === 0 && filter.lifecycle === "archived" && (
        <Empty className="sidebar-tree-note" title="No archived campaigns">
          Archive a campaign from its <code>⋯</code> menu to declutter the active list. Archives
          are reversible from this tab.
        </Empty>
      )}
      {loaded && shown.length === 0 && filter.lifecycle !== "archived" && (
        <Empty className="sidebar-tree-note" title="No campaigns yet">
          Hit <strong>+ New campaign</strong> above. Bring the data your pipeline gets wrong — the
          Potter reads it, sets up the first run, and starts improving.
        </Empty>
      )}
      {loaded && shown.length > 0 && (
        <ul className="sidebar-tree-list">
          <ForestRows entries={shown} />
        </ul>
      )}
    </div>
  );
}
