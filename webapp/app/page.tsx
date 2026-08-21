import { AccessGate } from "@/components/onboarding/AccessGate";
import { AllowanceSpent } from "@/components/onboarding/AllowanceSpent";
import { AppShell } from "@/components/shell/AppShell";
import { ConsentGate } from "@/components/onboarding/ConsentGate";
import { WelcomeLockoutModal } from "@/components/onboarding/WelcomeLockoutModal";
import { ErrorBoundary } from "@/components/ui";
import { ViewMemoryProvider } from "@/lib/view-memory";
import { WorkspaceProvider } from "@/lib/workspace";

export default function Home() {
  // ErrorBoundary wraps the provider so a crash inside WorkspaceProvider
  // itself is still caught.
  return (
    <ErrorBoundary>
      <WorkspaceProvider>
        {/* Per-campaign view memory. Above AppShell so the shell itself can read it —
            the sidebar's expand/collapse and the "put me back where I was" restore are
            both AppShell-level concerns. It stores only ids and UI keys, so it depends
            on neither selection nor the served tree. */}
        <ViewMemoryProvider>
          <AppShell />
        </ViewMemoryProvider>
        {/* Four overlays, all reading auth state and none of them chrome. The two
            gates are blocking (a blocked account, an unaccepted Terms) and mutually
            exclusive by construction; the sign-in prompt is dismissable and fires
            from any Log in / Sign up chip, of which there are several. The spent
            allowance is a notice rather than a gate — it says the runs are over, not
            that the results are. All four self-hide for anon-with-nothing-to-say. */}
        <AccessGate />
        <ConsentGate />
        <AllowanceSpent />
        <WelcomeLockoutModal />
      </WorkspaceProvider>
    </ErrorBoundary>
  );
}
