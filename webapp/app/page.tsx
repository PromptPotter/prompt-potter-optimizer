import { AccessGate } from "@/components/onboarding/AccessGate";
import { AppShell } from "@/components/shell/AppShell";
import { ConsentGate } from "@/components/onboarding/ConsentGate";
import { WelcomeLockoutModal } from "@/components/onboarding/WelcomeLockoutModal";
import { ErrorBoundary } from "@/components/ui";
import { ViewMemoryProvider } from "@/lib/state/view-memory";
import { WorkspaceProvider } from "@/lib/state/workspace";
import { mount } from "./mount";

mount(
    <ErrorBoundary>
      {/* The address reads view memory, so it sits above it. */}
      <ViewMemoryProvider>
        <WorkspaceProvider>
          <AppShell />
          <AccessGate />
          <ConsentGate />
          <WelcomeLockoutModal />
        </WorkspaceProvider>
      </ViewMemoryProvider>
    </ErrorBoundary>,
);
