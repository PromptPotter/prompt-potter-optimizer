import { AccessGate } from "@/components/onboarding/AccessGate";
import { AllowanceExhausted } from "@/components/onboarding/AllowanceExhausted";
import { AppShell } from "@/components/shell/AppShell";
import { ConsentGate } from "@/components/onboarding/ConsentGate";
import { WelcomeLockoutModal } from "@/components/onboarding/WelcomeLockoutModal";
import { ErrorBoundary } from "@/components/ui";
import { RegistryProvider } from "@/lib/registry";
import { ViewMemoryProvider } from "@/lib/view-memory";
import { WorkspaceProvider } from "@/lib/workspace";

export default function Home() {
  return (
    <ErrorBoundary>
      {/* The address reads the registry and view memory, so both sit above it. */}
      <RegistryProvider>
        <ViewMemoryProvider>
          <WorkspaceProvider>
            <AppShell />
            <AccessGate />
            <ConsentGate />
            <AllowanceExhausted />
            <WelcomeLockoutModal />
          </WorkspaceProvider>
        </ViewMemoryProvider>
      </RegistryProvider>
    </ErrorBoundary>
  );
}
