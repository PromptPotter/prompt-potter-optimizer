"use client";
import { useAuth } from "@/lib/auth-context";
import { useIngest } from "@/lib/ingest-flow";
import { useRegistry } from "@/lib/registry";
import { useWorkspace } from "@/lib/workspace";
import { cx } from "@/lib/cx";
import { WORKSPACE_LABEL, isWorkspaceTab } from "@/lib/view-tab";
import { CampaignMenu } from "@/components/shell/sidebar/CampaignMenu";
import s from "./MobileAppBar.module.css";

export function MobileAppBar() {
  const { status, openAuthPrompt } = useAuth();
  const { campaigns, runningCycles } = useRegistry();
  const { campaignId, tab, listScreen, showList } = useWorkspace();
  const { openComposer } = useIngest();
  const workspace = isWorkspaceTab(tab);

  if (listScreen) return null;

  const campaign = workspace ? undefined : campaigns.find((c) => c.campaign_id === campaignId);
  const title = workspace
    ? WORKSPACE_LABEL
    : campaign
      ? campaign.display_name
      : "PromptPotter";
  const anon = status === "unauthed";
  const running = runningCycles.length;

  return (
    // `mobile-appbar` is the global marker shell.css keys the ≤bp-md reveal on.
    <div className={cx("mobile-appbar", s.bar)}>
      <div className={s.row}>
        <button
          type="button"
          className={cx(s.icon, running > 0 && s.dotted)}
          aria-label={running > 0 ? `Campaigns — ${running} running` : "Campaigns"}
          onClick={showList}
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
            {campaign ? <CampaignMenu campaign={campaign} variant="standalone" /> : null}
            <button
              type="button"
              className={s.icon}
              aria-label="New campaign"
              title="New campaign"
              onClick={openComposer}
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
