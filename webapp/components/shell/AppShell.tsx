import type { CSSProperties } from "react";
import { useWorkspace } from "@/lib/workspace";
import { SIDEBAR_WIDTH, useSidebarCollapsed, useSidebarWidth } from "@/lib/prefs/sidebar";
import { cx } from "@/lib/cx";
import { VendorSprite } from "@/components/ui";
import { Sidebar } from "@/components/shell/sidebar/Sidebar";
import { SidebarResizer } from "@/components/shell/sidebar/SidebarResizer";
import { JobsDock } from "@/components/shell/JobsDock";
import { IngestFlowProvider } from "@/lib/state/ingest-flow";
import { ThreadProvider } from "@/lib/chat/thread";
import { CriticalAlertBanner } from "@/components/shell/CriticalAlertBanner";
import { RemoteControl } from "@/components/shell/remote/RemoteControl";
import { RunMasthead } from "@/components/shell/masthead/RunMasthead";
import { ViewTabs } from "@/components/shell/ViewTabs";
import { Views } from "@/components/shell/Views";

export function AppShell() {
  return (
    <ThreadProvider>
      <IngestFlowProvider>
        <AppShellInner />
      </IngestFlowProvider>
    </ThreadProvider>
  );
}

function AppShellInner() {
  const { viewed, views } = useWorkspace();
  const [sidebarCollapsed] = useSidebarCollapsed();
  // Applied inline only while expanded, so the collapsed rail's class rule still wins.
  const [sidebarWidth, setSidebarWidth] = useSidebarWidth();

  return (
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
        {viewed !== null && <ViewTabs />}
        <Views />
      </main>
      <RemoteControl />
      <VendorSprite />
    </div>
  );
}
