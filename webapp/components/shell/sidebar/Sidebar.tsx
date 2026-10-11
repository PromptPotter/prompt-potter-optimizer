"use client";
// The disabled footer search icon is an INTENTIONAL placeholder: never sweep it as non-functional.
import { useWorkspace } from "@/lib/workspace";
import { useAuth } from "@/lib/auth-context";
import { postLogout } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import { useIngest } from "@/lib/ingest-flow";
import { useSidebarCollapsed } from "@/lib/sidebar-prefs";
import { BRAND } from "@/lib/brand";
import { TERMS } from "@/lib/terms";
import { Term } from "@/components/ui";
import { PotterMark } from "@/components/brand/PotterMark";
import { applyTheme, readStoredTheme } from "@/lib/theme";
import { AccountSpend } from "./AccountSpend";
import { SidebarContent } from "./SidebarContent";
import { ViewGlyph } from "@/components/shell/ViewTabs";
import { WORKSPACE_TABS, tabLabel } from "@/lib/view-tab";

function flipTheme() {
  applyTheme(readStoredTheme() === "light" ? "dark" : "light");
}

export function Sidebar() {
  const { tab, openView: onOpenView, openAccount } = useWorkspace();
  const { compose: onNewCycle } = useIngest();
  const [collapsed, onToggleCollapse] = useSidebarCollapsed();

  const { status, openAuthPrompt } = useAuth();
  // Nothing polls the session; the navigation below is the read-back.
  const logout = useCommand<"logout">("sidebar-session", { revalidate: false });
  const handleSignOut = () =>
    void logout.run("logout", postLogout, () => {
      window.location.href = "/login/";
    });

  return (
    <nav className="sidebar" aria-label="Primary">
      <button
        type="button"
        className="sidebar-toggle"
        onClick={onToggleCollapse}
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-expanded={!collapsed}
      >
        {collapsed ? "›" : "‹"}
      </button>
      <div className="brand">
        <div className="brand-lockup">
          <div className="brand-mark">
            <PotterMark size={12} />
          </div>
          <span className="brand-name">PromptPotter</span>
        </div>
        <div className="brand-sub">
          <Term content={TERMS.brand_live_preview}>LIVE PREVIEW</Term>
        </div>
      </div>
      <div className="sidebar-primary">
        <button
          type="button"
          className="sidebar-cta"
          onClick={onNewCycle}
          title="Start a new campaign"
        >
          + New campaign
        </button>
        <div className="sidebar-views">
          {WORKSPACE_TABS.map((t) => (
            <button
              key={t}
              type="button"
              className="sidebar-view"
              aria-current={tab === t ? "page" : undefined}
              onClick={() => onOpenView(t)}
            >
              <ViewGlyph tab={t} />
              {tabLabel(t)}
            </button>
          ))}
        </div>
      </div>
      <SidebarContent />
      <AccountSpend />
      <div className="sidebar-footer">
        <div className="sidebar-footer-chrome">
          <button
            type="button"
            className="sidebar-search"
            aria-label="Search analytics (coming soon)"
            title="Search analytics"
            disabled
          >
            <svg width="16" height="16" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
              <circle cx="8" cy="8" r="5" />
              <line x1="12" y1="12" x2="15" y2="15" />
            </svg>
          </button>
          <button
            className="theme-toggle"
            type="button"
            onClick={flipTheme}
            title="Toggle bright / dark theme"
            aria-label="Toggle theme"
          >
            <svg className="sun" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
              <circle cx="8" cy="8" r="3" />
              <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.4 1.4M11.55 11.55l1.4 1.4M3.05 12.95l1.4-1.4M11.55 4.45l1.4-1.4" />
            </svg>
            <svg className="moon" width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
              <path d="M6 1.5A6.5 6.5 0 1 0 14.5 10 5 5 0 0 1 6 1.5z" />
            </svg>
          </button>
          {status === "authed" ? (
            <button
              type="button"
              className="account-trigger"
              aria-label="Open account"
              onClick={() => openAccount()}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
                <circle cx="8" cy="5.5" r="2.5" />
                <path d="M2.5 14c.8-2.5 3-4 5.5-4s4.7 1.5 5.5 4" />
              </svg>
            </button>
          ) : null}
        </div>
        {status === "unauthed" && (
          <div className="sidebar-footer-auth">
            <button
              type="button"
              className="auth-chip auth-chip-gold"
              onClick={openAuthPrompt}
            >
              Log in
            </button>
            <button
              type="button"
              className="auth-chip auth-chip-rust"
              onClick={openAuthPrompt}
            >
              Sign up for free
            </button>
          </div>
        )}
        <a
          className="sidebar-footer-item"
          href={BRAND.supportUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          Support
        </a>
        {status === "authed" && (
          <button
            type="button"
            className="sidebar-footer-item"
            onClick={handleSignOut}
            disabled={logout.pending !== null}
          >
            {logout.pending !== null ? "Signing out…" : "Log out"}
          </button>
        )}
      </div>
    </nav>
  );
}
