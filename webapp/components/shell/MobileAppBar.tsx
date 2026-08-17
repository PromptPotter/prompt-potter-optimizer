"use client";
import { useAuth } from "@/lib/auth-context";
import { useWorkspace } from "@/lib/workspace";
import { cx } from "@/lib/cx";
import { MORE_TABS, PRIMARY_TABS, isMoreTab, tabLabel, type Tab } from "@/lib/view-tab";
import { SegmentedControl, type Segment } from "@/components/ui";
import { CampaignMenu } from "@/components/shell/sidebar/CampaignMenu";
import s from "./MobileAppBar.module.css";

// The phone's app bar — the CAMPAIGN screen's chrome, and the only top bar this app
// has at any width. The LIST screen is the sidebar at full width, which carries its
// own brand, CTA, filter and footer, so it gets no bar and `←` moves between the two.
//
// `←` also carries the live-run dot, off the same `liveCycles` the desktop
// sidebar-edge dock reads (I6, one server-owned answer) — not a second dock.

interface Props {
  tab: Tab;
  onSelectTab: (tab: Tab) => void;
  listScreen: boolean;
  onBack: () => void;
  onNewCycle: () => void;
}

// The two primary views, PLUS whichever demoted view is active — that third segment
// is what keeps `SegmentedControl`'s "exactly one is always on" true while the
// operator reads Verify or Files, the same reason `› more` opens around it.
function segmentsFor(tab: Tab): readonly Segment<Tab>[] {
  const shown: Tab[] = isMoreTab(tab) ? [...PRIMARY_TABS, tab] : [...PRIMARY_TABS];
  return shown.map((t) => ({ value: t, label: tabLabel(t) }));
}

export function MobileAppBar({ tab, onSelectTab, listScreen, onBack, onNewCycle }: Props) {
  const { status, openAuthPrompt } = useAuth();
  const { campaignId, campaigns, liveCycles } = useWorkspace();

  // The list screen is the sidebar; it is its own header.
  if (listScreen) return null;

  const campaign = campaigns.find((c) => c.campaign_id === campaignId);
  const title = campaign?.label || campaign?.dataset_name || "PromptPotter";
  const anon = status === "unauthed";

  return (
    // `mobile-appbar` is the global marker shell.css keys the ≤bp-md reveal on;
    // `s.bar` carries only the look.
    <div className={cx("mobile-appbar", s.bar)}>
      <div className={s.row}>
        <button
          type="button"
          className={cx(s.icon, liveCycles.length > 0 && s.dotted)}
          aria-label={
            liveCycles.length > 0
              ? `Campaigns — ${liveCycles.length} active`
              : "Campaigns"
          }
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
            {/* ONE campaign menu in the app — the same component the sidebar rows
                use, with the two demoted views folded in above its verbs. */}
            {campaign ? (
              <CampaignMenu
                campaign={campaign}
                variant="standalone"
                leading={({ close }) =>
                  MORE_TABS.map((t) => (
                    <button
                      key={t}
                      type="button"
                      role="menuitem"
                      className="campaign-menu-item"
                      onClick={() => {
                        close();
                        onSelectTab(t);
                      }}
                    >
                      {tabLabel(t)}
                    </button>
                  ))
                }
              />
            ) : null}
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
      {/* The two views read constantly. Verify and Files are in the ⋯ above —
          same hierarchy as the sidebar, one source (lib/view-tab.ts). */}
      <SegmentedControl
        className={s.segments}
        options={segmentsFor(tab)}
        value={tab}
        onChange={onSelectTab}
        ariaLabel="Campaign view"
      />
    </div>
  );
}
