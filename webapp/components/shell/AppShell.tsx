import { useEffect, useMemo, type CSSProperties } from "react";
import { subjectKey } from "@/lib/api";
import { CycleStreamProvider } from "@/lib/poll";
import { ConnectorProvider } from "@/lib/hooks/useConnector";
import { useWorkspace } from "@/lib/workspace";
import { HardSamplesProvider } from "@/lib/hard-samples";
import { useLeafDatasetName } from "@/lib/hooks/useLeafDatasetName";
import { pathLeaf } from "@/lib/ids";
import { SIDEBAR_WIDTH, useSidebarCollapsed, useSidebarWidth } from "@/lib/prefs/sidebar";
import { applyChartDefaults } from "@/lib/theme";
import { cx } from "@/lib/cx";
import { VendorSprite } from "@/components/ui";
import { Sidebar } from "@/components/shell/sidebar/Sidebar";
import { SidebarResizer } from "@/components/shell/sidebar/SidebarResizer";
import { JobsDock } from "@/components/shell/JobsDock";
import { CompareSelectionProvider } from "@/lib/state/compare-selection";
import { SelectionProvider } from "@/lib/SelectionContext";
import { LineageProvider } from "@/lib/lineage";
import { IngestFlowProvider, useIngest } from "@/lib/ingest-flow";
import { ThreadProvider } from "@/lib/chat/thread";
import { CriticalAlertBanner } from "@/components/shell/CriticalAlertBanner";
import { RemoteControl } from "@/components/shell/remote/RemoteControl";
import { RunMasthead } from "@/components/shell/masthead/RunMasthead";
import { ViewTabs } from "@/components/shell/ViewTabs";
import { Views } from "@/components/shell/Views";

export function AppShell() {
  const { viewedPath } = useWorkspace();
  return (
    // Outside the cycle-keyed providers: a comparison spans campaigns.
    <CompareSelectionProvider>
      <CycleStreamProvider path={viewedPath}>
        <ThreadProvider>
          <IngestFlowProvider>
            <AppShellInner />
          </IngestFlowProvider>
        </ThreadProvider>
      </CycleStreamProvider>
    </CompareSelectionProvider>
  );
}

function AppShellInner() {
  const { viewedPath, campaignId, cycleId, leafCycleId, views } = useWorkspace();
  const { draft } = useIngest();
  // The chain is the hops ABOVE the leaf, so an L4 inner run resolves its OWN pipeline.
  const leafHop = viewedPath ? pathLeaf(viewedPath) : null;
  const connectorAt = useMemo(
    () =>
      leafHop && viewedPath
        ? subjectKey("course", [leafHop.campaignId, leafHop.cycleId], viewedPath.slice(0, -1))
        : null,
    [leafHop, viewedPath],
  );
  const leafDatasetName = useLeafDatasetName(
    viewedPath,
    leafHop ? leafHop.campaignId : null,
    connectorAt,
  );
  const [sidebarCollapsed] = useSidebarCollapsed();
  // Applied inline only while expanded, so the collapsed rail's class rule still wins.
  const [sidebarWidth, setSidebarWidth] = useSidebarWidth();

  useEffect(() => {
    applyChartDefaults();
  }, []);

  return (
    <SelectionProvider cycleId={leafCycleId}>
    <ConnectorProvider campaignId={leafHop?.campaignId ?? null} at={connectorAt} draft={draft}>
    {/* Inside SelectionProvider: it composes `sampleSet` into the masked read. */}
    <LineageProvider campaignId={campaignId} cycleId={cycleId}>
    <HardSamplesProvider path={viewedPath} datasetName={leafDatasetName}>
    <div
      className={cx(
        "shell",
        sidebarCollapsed && "sidebar-collapsed",
        views.at(-1)?.family === "campaigns" && "mobile-list",
      )}
      style={
        sidebarCollapsed
          ? undefined
          : ({ "--sidebar-width": `${sidebarWidth}px` } as CSSProperties)
      }
    >
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <Sidebar />
      {!sidebarCollapsed && (
        <SidebarResizer
          width={sidebarWidth}
          setWidth={setSidebarWidth}
          min={SIDEBAR_WIDTH.min}
          max={SIDEBAR_WIDTH.max}
        />
      )}
      {/* A `.shell` child, not a sidebar one: the sidebar clips its overflow. */}
      <JobsDock />
      <main className="main" id="main-content" tabIndex={-1}>
        <RunMasthead />
        <CriticalAlertBanner />
        {/* Only a run has presets: a check-in opens on its chat. */}
        {viewedPath !== null && <ViewTabs />}
        <Views />
      </main>
      <RemoteControl />
      <VendorSprite />
    </div>
    </HardSamplesProvider>
    </LineageProvider>
    </ConnectorProvider>
    </SelectionProvider>
  );
}
