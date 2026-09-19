"use client";
// Profile pane — who this account is, how it signs in, what it may do, and the session.
// A session with no provider is the local workspace (sign-in off, or the CLI's own identity),
// and every section says so rather than rendering an empty field.

import { useState } from "react";
import { AccountEmpty, AccountSection } from "./AccountSection";
import { PROVIDER_LABEL, ProviderIcon } from "./providers";
import { Badge, Button, CopyButton } from "@/components/ui";
import { postLogout, type MeResponse } from "@/lib/api";

const CAP_PREFIX = "campaign.";

export function AccountProfileTab({ me }: { me: MeResponse }) {
  return (
    <>
      <IdentityCard me={me} />
      <EmailSection me={me} />
      <SignInSection me={me} />
      <PermissionsSection capabilities={me.capabilities} />
      <SessionSection me={me} />
    </>
  );
}

function providerName(provider: string): string {
  return PROVIDER_LABEL[provider] ?? provider;
}

function IdentityCard({ me }: { me: MeResponse }) {
  const display = me.name ?? me.email;
  const initials = (display ?? "")
    .split(/\s|@/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
  return (
    <div className="account-identity">
      <div className="account-avatar" aria-hidden="true">
        {initials || <span className="account-avatar-glyph">&gt;_</span>}
      </div>
      <div className="account-identity-main">
        <span className="account-identity-name">{display ?? "Local workspace"}</span>
        <span className="account-identity-sub">
          {me.provider
            ? `Signed in with ${providerName(me.provider)}`
            : "Sign-in is off on this server"}
        </span>
        <span className="account-identity-id">
          <code>{me.user_id}</code>
          <CopyButton data={me.user_id} title="Copy the account id">
            Copy id
          </CopyButton>
        </span>
      </div>
      <div className="account-identity-side">
        <Badge tone={me.access_state === "active" ? "success" : "danger"}>
          {me.access_state === "active" ? "✓ Active" : "✕ Blocked"}
        </Badge>
        <Button variant="ghost" disabled title="Profile editing arrives in a later version">
          Update profile
        </Button>
      </div>
    </div>
  );
}

function EmailSection({ me }: { me: MeResponse }) {
  return (
    <AccountSection title="Email" lede="Where notices about this account go.">
      {me.email ? (
        <div className="account-line">
          <span className="account-strong">{me.email}</span>
          <Badge>Primary</Badge>
        </div>
      ) : (
        <AccountEmpty title="No email on this account">
          {me.provider
            ? `${providerName(me.provider)} did not share a verified address. Add one there and sign in again.`
            : "The local workspace has no sign-in, so there is no address to show. Turn sign-in on to give each person their own account."}
        </AccountEmpty>
      )}
    </AccountSection>
  );
}

function SignInSection({ me }: { me: MeResponse }) {
  return (
    <AccountSection title="Sign-in" lede="The provider that proves this account is you.">
      {me.connected_accounts.length > 0 ? (
        <ul className="account-providers">
          {me.connected_accounts.map((acc) => (
            <li key={acc.provider} className="account-provider">
              <ProviderIcon provider={acc.provider} />
              <span className="account-strong">{providerName(acc.provider)}</span>
              <span className="account-provider-email">{acc.email ?? "no address shared"}</span>
              <Button
                variant="ghost"
                disabled
                title="Removing the only connected account locks you out"
              >
                Remove
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <AccountEmpty title="No sign-in provider">
          This server runs with sign-in off, so whoever can reach it acts as this workspace. Put
          it behind Google or GitHub sign-in before sharing the address.
        </AccountEmpty>
      )}
      {me.provider && me.available_providers.length > 0 ? (
        <div className="account-connect">
          <Button disabled title="Account linking arrives in a later version">
            + Connect account
          </Button>
          <span className="account-note">
            Also offered here:{" "}
            {me.available_providers.map((name) => providerName(name)).join(", ")}. Until linking
            lands, signing in with one of them opens a separate account.
          </span>
        </div>
      ) : null}
    </AccountSection>
  );
}

function PermissionsSection({ capabilities }: { capabilities: string[] }) {
  return (
    <AccountSection
      title="Permissions"
      lede="What this session may ask the server to do. The server enforces them; this list reflects them."
    >
      {capabilities.length > 0 ? (
        <ul className="account-caps">
          {capabilities.map((cap) => (
            <li key={cap} title={cap}>
              {cap.startsWith(CAP_PREFIX) ? cap.slice(CAP_PREFIX.length) : cap}
            </li>
          ))}
        </ul>
      ) : (
        <AccountEmpty title="No permissions">
          This account can read what it already has but cannot start, steer or change anything.
          Whoever runs this server grants access.
        </AccountEmpty>
      )}
    </AccountSection>
  );
}

function SessionSection({ me }: { me: MeResponse }) {
  const [signingOut, setSigningOut] = useState(false);
  const [failed, setFailed] = useState(false);

  const signOut = async () => {
    setSigningOut(true);
    setFailed(false);
    try {
      await postLogout();
      window.location.href = "/login";
    } catch {
      setFailed(true);
      setSigningOut(false);
    }
  };

  if (!me.provider) {
    return (
      <AccountSection title="Session">
        <p className="account-note">
          There is no session to end: with sign-in off, this browser is the workspace.
        </p>
      </AccountSection>
    );
  }
  return (
    <AccountSection
      title="Session"
      lede="Signing out ends this browser's session. Running campaigns keep running."
      aside={
        <Button variant="danger" onClick={() => void signOut()} disabled={signingOut}>
          {signingOut ? "Signing out…" : "Sign out"}
        </Button>
      }
    >
      {failed ? (
        <p className="account-failure" role="alert">
          The server did not end the session. Try again.
        </p>
      ) : null}
    </AccountSection>
  );
}
