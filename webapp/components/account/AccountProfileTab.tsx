"use client";
// Profile pane — avatar + name + email + connected accounts + "Connect account".

import { useState } from "react";
import { PROVIDER_LABEL, ProviderIcon } from "./providers";
import type { MeResponse } from "@/lib/api";

export function AccountProfileTab({ me }: { me: MeResponse }) {
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
