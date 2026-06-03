"use client";
// Clerk-style account modal — Profile / Security / Activity panes over
// `/auth/*`.
//
// Profile: avatar + name + email + connected accounts + "Connect account".
// Security: provider badge + quota status (spend / concurrent / daily) +
//           sign-out.
// Activity: 3 vertically-stacked time-bucketed charts — spend / requests /
//           tokens — over a selectable window.

import { useEffect, useState } from "react";
import { AboutUnit } from "./AboutUnit";
import { applyTheme, readStoredTheme, useThemeVersion } from "@/lib/theme";
import {
  fetchActivity,
  fetchMe,
  fetchQuotaStatus,
  fetchUserSettings,
  patchUserSettings,
  postLogout,
  type ActivityBucket,
  type ActivityGroupBy,
  type ActivityResponse,
  type ActivityWindow,
  type MeResponse,
  type QuotaStatus,
} from "@/lib/api";

interface Props {
  open: boolean;
  onClose: () => void;
}

type AccountTab = "profile" | "security" | "activity" | "preferences" | "about";

const PROVIDER_LABEL: Record<string, string> = {
  google: "Google",
  github: "GitHub",
};

const ACTIVITY_WINDOWS: { id: ActivityWindow; label: string }[] = [
  { id: "15m", label: "Past 15 min" },
  { id: "30m", label: "Past 30 min" },
  { id: "1h", label: "Past hour" },
  { id: "3h", label: "Past 3 hours" },
  { id: "1d", label: "Past day" },
  { id: "2d", label: "Past 2 days" },
  { id: "1w", label: "Past week" },
  { id: "1mo", label: "Past month" },
  { id: "1y", label: "Past year" },
];

export function AccountModal({ open, onClose }: Props) {
  const [me, setMe] = useState<MeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<AccountTab>("profile");
  // Render-phase reset on (re-)open so stale error / data from a prior
  // session don't flash into view. The sanctioned alternative to a
  // setState-in-effect (see webapp CLAUDE.md).
  const [prevOpen, setPrevOpen] = useState(open);
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) {
      setError(null);
      setMe(null);
    }
  }

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchMe()
      .then((data) => {
        if (!cancelled) setMe(data);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="account-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Account"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="account-modal">
        <nav className="account-nav" aria-label="Account sections">
          <div className="account-nav-head">
            <h2>Account</h2>
            <p>Manage your account info.</p>
          </div>
          <ul className="account-nav-list">
            <li>
              <button
                type="button"
                className={`account-nav-item${tab === "profile" ? " active" : ""}`}
                onClick={() => setTab("profile")}
              >
                Profile
              </button>
            </li>
            <li>
              <button
                type="button"
                className={`account-nav-item${tab === "security" ? " active" : ""}`}
                onClick={() => setTab("security")}
              >
                Security
              </button>
            </li>
            <li>
              <button
                type="button"
                className={`account-nav-item${tab === "activity" ? " active" : ""}`}
                onClick={() => setTab("activity")}
              >
                Activity
              </button>
            </li>
            <li>
              <button
                type="button"
                className={`account-nav-item${tab === "preferences" ? " active" : ""}`}
                onClick={() => setTab("preferences")}
              >
                Preferences
              </button>
            </li>
            <li>
              <button
                type="button"
                className={`account-nav-item${tab === "about" ? " active" : ""}`}
                onClick={() => setTab("about")}
              >
                About this unit
              </button>
            </li>
          </ul>
        </nav>
        <section className="account-pane">
          <header className="account-pane-head">
            <h3>
              {tab === "profile"
                ? "Profile details"
                : tab === "security"
                  ? "Security"
                  : tab === "preferences"
                    ? "Preferences"
                    : tab === "about"
                      ? "About this unit"
                      : "Activity"}
            </h3>
            <button
              type="button"
              className="account-close"
              aria-label="Close"
              onClick={onClose}
            >
              ×
            </button>
          </header>
          <div className="account-pane-body">
            {error ? <p className="account-error">{error}</p> : null}
            {!me && !error && (tab === "profile" || tab === "security") ? (
              <p className="account-loading">Loading…</p>
            ) : null}
            {me && tab === "profile" ? <ProfileTab me={me} /> : null}
            {me && tab === "security" ? <SecurityTab me={me} /> : null}
            {open && tab === "activity" ? <ActivityTab /> : null}
            {open && tab === "preferences" ? <PreferencesTab /> : null}
            {open && tab === "about" ? <AboutUnit /> : null}
          </div>
        </section>
      </div>
    </div>
  );
}

function PreferencesTab() {
  const [demo, setDemo] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

function ProfileTab({ me }: { me: MeResponse }) {
  return (
    <>
      <ProfileRow me={me} />
      <EmailRow email={me.email} />
      <ConnectedAccountsRow me={me} />
    </>
  );
}

function ProfileRow({ me }: { me: MeResponse }) {
  const initials = (me.name ?? me.email ?? "?")
    .split(/\s|@/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
  return (
    <div className="account-row account-row-profile">
      <span className="account-label">Profile</span>
      <div className="account-row-main">
        <div className="account-avatar" aria-hidden="true">
          {initials || "?"}
        </div>
        <div className="account-name">{me.name ?? me.email ?? "—"}</div>
        <button
          type="button"
          className="account-action-link"
          disabled
          title="Coming in a later version"
        >
          Update profile
        </button>
      </div>
    </div>
  );
}

function EmailRow({ email }: { email: string | null }) {
  return (
    <div className="account-row">
      <span className="account-label">Email addresses</span>
      <div className="account-row-main">
        {email ? (
          <div className="account-email-line">
            <span className="account-email">{email}</span>
            <span className="account-badge">Primary</span>
          </div>
        ) : (
          <span className="account-muted">—</span>
        )}
      </div>
    </div>
  );
}

function ConnectedAccountsRow({ me }: { me: MeResponse }) {
  const [openMenuFor, setOpenMenuFor] = useState<string | null>(null);
  return (
    <div className="account-row">
      <span className="account-label">Connected accounts</span>
      <div className="account-row-main">
        <ul className="account-providers">
          {me.connected_accounts.map((acc) => (
            <li key={acc.provider} className="account-provider">
              <ProviderIcon provider={acc.provider} />
              <span className="account-provider-name">
                {PROVIDER_LABEL[acc.provider] ?? acc.provider}
              </span>
              <span className="account-provider-email">{acc.email ?? ""}</span>
              <div className="account-provider-menu">
                <button
                  type="button"
                  className="account-menu-trigger"
                  aria-label={`Manage ${PROVIDER_LABEL[acc.provider] ?? acc.provider}`}
                  onClick={() =>
                    setOpenMenuFor(openMenuFor === acc.provider ? null : acc.provider)
                  }
                >
                  ⋯
                </button>
                {openMenuFor === acc.provider ? (
                  <div className="account-menu-popover" role="menu">
                    <button
                      type="button"
                      className="account-menu-item"
                      disabled
                      title="Removing the only connected account locks you out"
                    >
                      Remove
                    </button>
                  </div>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
        {me.available_providers.length > 0 ? (
          <div className="account-add-provider">
            <button
              type="button"
              className="account-add-trigger"
              onClick={() =>
                alert(
                  "Account linking ships in a later version. For now, signing in with another provider creates a separate account.",
                )
              }
            >
              <span className="account-add-icon">+</span>
              <span>Connect account</span>
            </button>
            <div className="account-add-options" aria-hidden="true">
              {me.available_providers.map((name) => (
                <span key={name} className="account-add-option">
                  <ProviderIcon provider={name} />
                  {PROVIDER_LABEL[name] ?? name}
                </span>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function SecurityTab({ me }: { me: MeResponse }) {
  const [quota, setQuota] = useState<QuotaStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [signingOut, setSigningOut] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchQuotaStatus()
      .then((q) => {
        if (!cancelled) setQuota(q);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSignOut = async () => {
    setSigningOut(true);
    try {
      await postLogout();
      window.location.href = "/login";
    } catch (e) {
      setError(String(e));
      setSigningOut(false);
    }
  };

  return (
    <>
      <div className="account-row">
        <span className="account-label">Sign-in provider</span>
        <div className="account-row-main">
          <span className="account-badge account-badge-strong">
            {me.provider ? PROVIDER_LABEL[me.provider] ?? me.provider : "—"}
          </span>
        </div>
      </div>
      <div className="account-row">
        <span className="account-label">Quota status</span>
        <div className="account-row-main">
          {error ? <p className="account-error">{error}</p> : null}
          {!quota && !error ? (
            <p className="account-muted">Loading…</p>
          ) : null}
          {quota ? <QuotaCard quota={quota} /> : null}
        </div>
      </div>
      <div className="account-row">
        <span className="account-label">Session</span>
        <div className="account-row-main">
          <button
            type="button"
            className="account-signout"
            onClick={handleSignOut}
            disabled={signingOut}
          >
            {signingOut ? "Signing out…" : "Sign out"}
          </button>
        </div>
      </div>
    </>
  );
}

function QuotaCard({ quota }: { quota: QuotaStatus }) {
  const spendCap = quota.spend_budget_usd_daily;
  return (
    <ul className="quota-grid">
      <li className="quota-cell">
        <span className="quota-cell-label">Spend today</span>
        <span className="quota-cell-value">
          ${quota.spend_used_today_usd.toFixed(2)}
        </span>
        <span className="quota-cell-sub">
          {spendCap !== null ? `of $${spendCap.toFixed(2)} daily cap` : "no daily cap"}
        </span>
      </li>
      <li className="quota-cell">
        <span className="quota-cell-label">Concurrent cycles</span>
        <span className="quota-cell-value">
          {quota.concurrent_running} / {quota.max_concurrent_cycles}
        </span>
        <span className="quota-cell-sub">running now</span>
      </li>
      <li className="quota-cell">
        <span className="quota-cell-label">Campaigns today</span>
        <span className="quota-cell-value">
          {quota.campaigns_today} / {quota.max_campaigns_per_day}
        </span>
        <span className="quota-cell-sub">since UTC midnight</span>
      </li>
    </ul>
  );
}

function ActivityTab() {
  const [window, setWindow] = useState<ActivityWindow>("1d");
  const [groupBy, setGroupBy] = useState<ActivityGroupBy>("model");
  const [data, setData] = useState<ActivityResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Render-phase reset when window or group_by changes so the old
  // buckets don't briefly render against the new axis labels.
  const [prevKey, setPrevKey] = useState(`${window}|${groupBy}`);
  const key = `${window}|${groupBy}`;
  if (key !== prevKey) {
    setPrevKey(key);
    setData(null);
    setError(null);
  }

  useEffect(() => {
    let cancelled = false;
    fetchActivity(window, groupBy)
      .then((r) => {
        if (!cancelled) setData(r);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [window, groupBy]);

  const labels = data?.series_labels ?? [];
  const palette = _palette(labels.length);
  return (
    <>
      <div className="activity-window-row">
        <label htmlFor="activity-window-select">Window</label>
        <select
          id="activity-window-select"
          value={window}
          onChange={(e) => setWindow(e.target.value as ActivityWindow)}
        >
          {ACTIVITY_WINDOWS.map((w) => (
            <option key={w.id} value={w.id}>
              {w.label}
            </option>
          ))}
        </select>
        <label htmlFor="activity-group-select">Color by</label>
        <select
          id="activity-group-select"
          value={groupBy}
          onChange={(e) => setGroupBy(e.target.value as ActivityGroupBy)}
        >
          <option value="model">By Model</option>
          <option value="api_key">By API Key</option>
        </select>
      </div>
      {error ? <p className="account-error">{error}</p> : null}
      {labels.length > 0 ? (
        <ul className="activity-legend">
          {labels.map((label, i) => (
            <li key={label}>
              <span
                className="activity-legend-swatch"
                style={{ background: palette[i] }}
                aria-hidden="true"
              />
              <span className="activity-legend-label">{label}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <ActivityBarChart
        title="Spend"
        valueLabel={data ? `$${data.total_spend_usd.toFixed(2)}` : "$0.00"}
        buckets={data?.buckets ?? []}
        labels={labels}
        palette={palette}
        accessor={(b) => b.series_spend}
        totalAccessor={(b) => b.spend_usd}
      />
      <ActivityBarChart
        title="Requests"
        valueLabel={data ? data.total_requests.toLocaleString() : "0"}
        buckets={data?.buckets ?? []}
        labels={labels}
        palette={palette}
        accessor={(b) => b.series_requests}
        totalAccessor={(b) => b.requests}
      />
      <ActivityBarChart
        title="Tokens"
        valueLabel={data ? _formatCompact(data.total_tokens) : "0"}
        buckets={data?.buckets ?? []}
        labels={labels}
        palette={palette}
        accessor={(b) => b.series_tokens}
        totalAccessor={(b) => b.tokens}
      />
    </>
  );
}

interface ActivityBarChartProps {
  title: string;
  valueLabel: string;
  buckets: ActivityBucket[];
  labels: string[];
  palette: string[];
  accessor: (b: ActivityBucket) => Record<string, number>;
  totalAccessor: (b: ActivityBucket) => number;
}

function ActivityBarChart({
  title,
  valueLabel,
  buckets,
  labels,
  palette,
  accessor,
  totalAccessor,
}: ActivityBarChartProps) {
  const totals = buckets.map(totalAccessor);
  const max = totals.reduce((m, v) => (v > m ? v : m), 0);
  const hasData = max > 0;
  const w = 600;
  const h = 90;
  const padX = 4;
  const padY = 6;
  const innerW = w - padX * 2;
  const innerH = h - padY * 2;
  const n = Math.max(1, buckets.length);
  const slotW = innerW / n;
  const barGap = 1.5;
  const barW = Math.max(1, slotW - barGap);
  return (
    <section className="activity-chart">
      <header className="activity-chart-head">
        <h4>{title}</h4>
        <span className="activity-chart-total">{valueLabel}</span>
      </header>
      <div className="activity-chart-body">
        {hasData ? (
          <svg
            className="activity-chart-svg"
            viewBox={`0 0 ${w} ${h}`}
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            {buckets.map((b, i) => {
              const series = accessor(b);
              let yOffset = 0;
              return (
                <g key={i} transform={`translate(${padX + i * slotW + barGap / 2}, 0)`}>
                  {labels.map((label, li) => {
                    const v = series[label] ?? 0;
                    if (v <= 0) return null;
                    const segH = (v / max) * innerH;
                    const y = padY + (innerH - yOffset - segH);
                    yOffset += segH;
                    return (
                      <rect
                        key={label}
                        x={0}
                        y={y}
                        width={barW}
                        height={segH}
                        fill={palette[li]}
                      />
                    );
                  })}
                </g>
              );
            })}
          </svg>
        ) : (
          <p className="activity-chart-empty">No data in this window</p>
        )}
      </div>
    </section>
  );
}

const _CHART_PALETTE = [
  "#3b82f6",
  "#7c5cff",
  "#00b894",
  "#f59e0b",
  "#ef4444",
  "#06b6d4",
  "#ec4899",
  "#84cc16",
  "#a855f7",
  "#14b8a6",
];

function _palette(n: number): string[] {
  if (n <= _CHART_PALETTE.length) return _CHART_PALETTE.slice(0, n);
  // Repeat the palette when there are more labels than colours; rare in practice.
  const out: string[] = [];
  for (let i = 0; i < n; i += 1) out.push(_CHART_PALETTE[i % _CHART_PALETTE.length]);
  return out;
}

function _formatCompact(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  return v.toLocaleString();
}

function ProviderIcon({ provider }: { provider: string }) {
  if (provider === "google") {
    return (
      <svg
        className="account-provider-icon"
        width="18"
        height="18"
        viewBox="0 0 18 18"
        aria-hidden="true"
      >
        <path
          fill="#4285F4"
          d="M17.64 9.205c0-.638-.057-1.252-.164-1.841H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.258h2.908c1.702-1.567 2.684-3.874 2.684-6.614z"
        />
        <path
          fill="#34A853"
          d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.859-3.048.859-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z"
        />
        <path
          fill="#FBBC05"
          d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z"
        />
        <path
          fill="#EA4335"
          d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z"
        />
      </svg>
    );
  }
  if (provider === "github") {
    return (
      <svg
        className="account-provider-icon"
        width="18"
        height="18"
        viewBox="0 0 16 16"
        aria-hidden="true"
      >
        <path
          fill="currentColor"
          d="M8 0C3.58 0 0 3.58 0 8a8 8 0 0 0 5.47 7.59c.4.07.55-.17.55-.38v-1.33c-2.22.48-2.69-1.07-2.69-1.07-.36-.92-.89-1.16-.89-1.16-.73-.5.06-.49.06-.49.8.06 1.22.82 1.22.82.72 1.22 1.87.87 2.33.66.07-.52.28-.87.5-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.61 7.61 0 0 1 4 0c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.74.54 1.48v2.2c0 .21.15.46.55.38A8 8 0 0 0 16 8c0-4.42-3.58-8-8-8z"
        />
      </svg>
    );
  }
  return <span className="account-provider-icon">●</span>;
}
