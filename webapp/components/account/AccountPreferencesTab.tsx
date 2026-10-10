"use client";

import { AccountSection } from "./AccountSection";
import { Switch } from "@/components/ui";
import { patchUserSettings, userSettingsRead } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import { shownData, useRead } from "@/lib/hooks/useRead";
import { invalidateReads } from "@/lib/read-cache";
import { applyTheme, readStoredTheme, useThemeVersion } from "@/lib/theme";
import { useShowCandidates } from "@/lib/tree-prefs";

export function AccountPreferencesTab() {
  const read = useRead(userSettingsRead());
  // No blanket revalidate: the write re-asks the one read carrying the setting.
  const cmd = useCommand<"user-settings">("preferences", { revalidate: false });
  const demo = shownData(read)?.demo_mode_enabled ?? null;

  const toggle = (next: boolean) =>
    void cmd.run(
      "user-settings",
      () => patchUserSettings({ demo_mode_enabled: next }),
      () => invalidateReads("user-settings"),
    );

  const busy = cmd.pending !== null;
  const error =
    cmd.failure?.message ??
    (read.status === "failed" ? "Could not read this setting. Reopen the pane to retry." : null);

  return (
    <>
      <AccountSection
        title="Try & learn"
        lede="A small support-ticket dataset, ready to optimize, in your collection. Turn it off once you are set up."
        aside={
          <Switch
            label="Show the try-and-learn demo dataset"
            checked={demo ?? false}
            locked={demo === null || busy}
            lockedNote={busy ? "saving" : "reading"}
            onChange={() => {
              if (demo !== null) toggle(!demo);
            }}
          />
        }
      >
        {error ? (
          <p className="account-failure" role="alert">
            {error}
          </p>
        ) : null}
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
      lede="Off by default, so a campaign is the last row of its branch — no ▶, and its candidates are read on the dashboard, where the chart plots them against each other. Turn this on to open a campaign into C0, C1.1, C1.2 … in the sidebar instead: worth it for a self-optimization run, where each candidate contains a whole inner run. Kept on this device only."
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
