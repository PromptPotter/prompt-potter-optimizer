"use client";
import { useAuth } from "@/lib/auth-context";
import { useWorkspace } from "@/lib/workspace";
import { cx } from "@/lib/cx";
import { CampaignMenu } from "@/components/shell/sidebar/CampaignMenu";
import s from "./MobileAppBar.module.css";

// The phone's app bar — the CAMPAIGN screen's chrome: back to the list, the campaign's
// name, and its verbs. The LIST screen is the sidebar at full width, which carries its
// own brand, CTA, filter and footer, so it gets no bar and `←` moves between the two.
//
// The VIEW axis is not here — ViewTabs owns it, as the bottom tab bar at this
// width, so this bar has no segments and no views in its `⋯`.
//
// `←` also carries the live-run dot, off the same `runningCycles` the desktop
// sidebar-edge dock reads (I6, one server-owned answer) — not a second dock.

interface Props {
  listScreen: boolean;
  onBack: () => void;
  onNewCycle: () => void;
}

export function MobileAppBar({ listScreen, onBack, onNewCycle }: Props) {
  const { status, openAuthPrompt } = useAuth();
  const { campaignId, campaigns, runningCycles } = useWorkspace();

  // The list screen is the sidebar; it is its own header.
  if (listScreen) return null;

  const campaign = campaigns.find((c) => c.campaign_id === campaignId);
  const title = campaign?.label || campaign?.dataset_name || "PromptPotter";
  const anon = status === "unauthed";
  const running = runningCycles.length;

  return (
    // `mobile-appbar` is the global marker shell.css keys the ≤bp-md reveal on;
    // `s.bar` carries only the look.
    <div className={cx("mobile-appbar", s.bar)}>
      <div className={s.row}>
        <button
          type="button"
          className={cx(s.icon, running > 0 && s.dotted)}
          aria-label={running > 0 ? `Campaigns — ${running} running` : "Campaigns"}
          onClick={onBack}
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M11.5 5 6.5 10l5 5" />
          </svg>
        </button>
        <span className={s.title}>{title}</span>
        {anon ? (
          <>
            <button type="button" className="auth-chip auth-chip-gold" onClick={openAuthPrompt}>
              Log in
            </button>
            <button type="button" className="auth-chip auth-chip-rust" onClick={openAuthPrompt}>
              Sign up
            </button>
          </>
        ) : (
          <>
            {/* ONE campaign menu in the app — the same component the sidebar rows use. */}
            {campaign ? <CampaignMenu campaign={campaign} variant="standalone" /> : null}
            <button
              type="button"
              className={s.icon}
              aria-label="New campaign"
              title="New campaign"
              onClick={onNewCycle}
            >
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M3 5.5A1.5 1.5 0 0 1 4.5 4h7A1.5 1.5 0 0 1 13 5.5v4A1.5 1.5 0 0 1 11.5 11H6l-3 2.5z" />
                <path d="M16 4.5v4M18 6.5h-4" />
              </svg>
            </button>
          </>
        )}
      </div>
    </div>
  );
}
