"use client";

import { AccountSection } from "./AccountSection";
import { FailureLine, ReadState, Switch } from "@/components/ui";
import { patchUserSettings, userSettingsRead } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import { useRead } from "@/lib/hooks/useRead";
import { invalidateReads } from "@/lib/read-cache";
import { applyTheme, readStoredTheme, useThemeVersion } from "@/lib/theme";
import { useShowCandidates } from "@/lib/prefs/tree";

export function AccountPreferencesTab() {
  const read = useRead(userSettingsRead());
  // No blanket revalidate: the write re-asks the one read carrying the setting.
  const cmd = useCommand<"user-settings">("preferences", { revalidate: false });

  const toggle = (next: boolean) =>
    void cmd.run(
      "user-settings",
      () => patchUserSettings({ demo_mode_enabled: next }),
      () => invalidateReads("user-settings"),
    );

  const busy = cmd.pending !== null;

  return (
    <>
      <AccountSection
        title="Try & learn"
        lede="A small support-ticket dataset, ready to optimize, in your collection. Turn it off once you are set up."
        aside={
          <ReadState read={read} subject="this setting">
            {(settings) => (
              <Switch
                label="Show the try-and-learn demo dataset"
                checked={settings.demo_mode_enabled}
                locked={busy}
                lockedNote="saving"
                onChange={() => toggle(!settings.demo_mode_enabled)}
              />
            )}
          </ReadState>
        }
      >
        <FailureLine failure={cmd.failure} />
      </AccountSection>
      <CampaignTreeSection />
      <ThemeSection />
    </>
  );
}

function CampaignTreeSection() {
  const [show, setShow] = useShowCandidates();
  return (
    <AccountSection
      title="Campaign tree"
      lede="Off by default, so a campaign is the last row of its branch — no ▶, and its candidates are read on the dashboard, where the chart plots them against each other. Turn this on to open a campaign into C0, C1.1, C1.2 … in the sidebar instead. A self-optimization run opens either way, since each of its candidates contains a whole inner run. Kept on this device only."
      aside={
        <Switch
          label="Open campaigns into their candidates"
          checked={show}
          onChange={() => setShow(!show)}
        />
      }
    />
  );
}

// Here as well as the navbar because phones hide the navbar toggle.
function ThemeSection() {
  useThemeVersion();
  const dark = readStoredTheme() === "dark";
  return (
    <AccountSection
      title="Operator mode"
      lede="Light is the default. Dark is the dense operator view for long optimization sessions. Kept on this device only."
      aside={
        <Switch
          label="Dark operator mode"
          checked={dark}
          onChange={() => applyTheme(dark ? "light" : "dark")}
        />
      }
    />
  );
}
