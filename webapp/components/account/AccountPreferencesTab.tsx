"use client";
// Preferences pane — try-and-learn demo dataset toggle (server setting) +
// appearance/theme (client-only, per-device).

import { useEffect, useState } from "react";
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
      .catch((e) => {
        if (!cancelled) setError(String(e));
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
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="account-row">
        <span className="account-label">Try &amp; learn</span>
        <div className="account-row-main">
          <label className="account-pref-toggle">
            <input
              type="checkbox"
              checked={demo ?? false}
              disabled={demo === null || busy}
              onChange={(e) => void toggle(e.target.checked)}
            />
            <span>Show the try-and-learn demo dataset</span>
          </label>
          <p className="account-muted">
            A small support-ticket dataset, ready to optimize, in your collection.
            Turn it off once you&rsquo;re set up.
          </p>
          {error ? <p className="account-error">{error}</p> : null}
        </div>
      </div>
      <ThemeRow />
    </>
  );
}

// Theme lives in settings (not the navbar) so it's reachable the same way on
// every device — on phones the standalone navbar toggle is hidden. Client-only
// state via lib/theme.ts; deliberately not a server-side user setting.
function ThemeRow() {
  useThemeVersion();
  const dark = readStoredTheme() === "dark";
  return (
    <div className="account-row">
      <span className="account-label">Appearance</span>
      <div className="account-row-main">
        <label className="account-pref-toggle">
          <input
            type="checkbox"
            checked={dark}
            onChange={(e) => applyTheme(e.target.checked ? "dark" : "light")}
          />
          <span>Dark mode &mdash; DOOM/lava operator view</span>
        </label>
        <p className="account-muted">
          The light, editorial register is the default. Switch to dark for deep
          operator work; the choice is remembered on this device.
        </p>
      </div>
    </div>
  );
}
