"use client";
// Preferences pane — try-and-learn demo dataset toggle (server setting) +
// appearance/theme (client-only, per-device).

import { useEffect, useState } from "react";
import { AccountSection } from "./AccountSection";
import { Switch } from "@/components/ui";
import { fetchUserSettings, patchUserSettings } from "@/lib/api";
import { applyTheme, readStoredTheme, useThemeVersion } from "@/lib/theme";

export function AccountPreferencesTab() {
  const [demo, setDemo] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Hand-rolled, not useFetch: `demo` is mutable local state the toggle below
  // writes after each PATCH, not a read-only fetch result — the server load
  // only seeds it.
  useEffect(() => {
    let cancelled = false;
    fetchUserSettings()
      .then((s) => {
        if (!cancelled) setDemo(s.demo_mode_enabled);
      })
      .catch(() => {
        if (!cancelled) setError("Could not read this setting. Reopen the pane to retry.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = async (next: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const s = await patchUserSettings({ demo_mode_enabled: next });
      setDemo(s.demo_mode_enabled);
    } catch {
      setError("The server did not save the change. Try again.");
    } finally {
      setBusy(false);
    }
  };

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
              if (demo !== null) void toggle(!demo);
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
      <ThemeSection />
    </>
  );
}

// Theme lives in settings (not the navbar) so it's reachable the same way on
// every device — on phones the standalone navbar toggle is hidden. Client-only
// state via lib/theme.ts; deliberately not a server-side user setting.
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
