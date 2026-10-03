import { AccessGate } from "@/components/onboarding/AccessGate";
import { AllowanceSpent } from "@/components/onboarding/AllowanceSpent";
import { AppShell } from "@/components/shell/AppShell";
import { ConsentGate } from "@/components/onboarding/ConsentGate";
import { WelcomeLockoutModal } from "@/components/onboarding/WelcomeLockoutModal";
import { ErrorBoundary } from "@/components/ui";
import { ViewMemoryProvider } from "@/lib/view-memory";
import { WorkspaceProvider } from "@/lib/workspace";

export default function Home() {
  return (
    <ErrorBoundary>
      <WorkspaceProvider>

        <ViewMemoryProvider>
          <AppShell />
        </ViewMemoryProvider>
        {/* The two gates are mutually exclusive by construction; all four overlays self-hide
            for an anon with nothing to say. */}
        <AccessGate />
        <ConsentGate />
        <AllowanceSpent />
        <WelcomeLockoutModal />
      </WorkspaceProvider>
    </ErrorBoundary>
  );
}
