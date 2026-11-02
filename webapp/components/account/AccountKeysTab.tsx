"use client";

import { AccountSection } from "./AccountSection";
import { Button, CommitInput, Empty, FailureLine, ReadState } from "@/components/ui";
import {
  couponRead,
  deleteApiKey,
  putApiKey,
  type AccountKey,
  type CouponStatus,
} from "@/lib/api";
import { COUPON_STATE_INFO } from "@/lib/api/types.generated";
import { fmtAgo } from "@/lib/format";
import { useCommand, type CommandFailure } from "@/lib/hooks/useCommand";
import { useRead } from "@/lib/hooks/useRead";
import { invalidateReads } from "@/lib/read-cache";

export function AccountKeysTab() {
  const read = useRead(couponRead(), { auth: true });
  return (
    <ReadState read={read} subject="your provider keys">
      {(coupon) => <ProviderKeys coupon={coupon} />}
    </ReadState>
  );
}

type KeyVerb = "save-key" | "remove-key";

// Only a flat denial needs a sentence here: the server words its own refusals, and 403 and 404 are one answer.
function refusal(f: CommandFailure): string | null {
  return f.kind === "denied" || f.kind === "gone"
    ? "This session may not change provider keys."
    : null;
}

export function ProviderKeys({ coupon }: { coupon: CouponStatus }) {
  // No blanket revalidate: the write re-asks the one read carrying the keys.
  const cmd = useCommand<KeyVerb>("account-keys", { describe: refusal, revalidate: false });
  const busy = cmd.pending !== null;
  const held = new Map(coupon.keys.map((k) => [k.provider, k]));

  const save = (provider: string, secret: string) =>
    void cmd.run(
      "save-key",
      () => putApiKey(provider, secret),
      () => invalidateReads("coupon"),
    );
  const remove = (provider: string) =>
    void cmd.run(
      "remove-key",
      () => deleteApiKey(provider),
      () => invalidateReads("coupon"),
    );

  return (
    <AccountSection title="Your provider keys" lede={COUPON_STATE_INFO[coupon.state].lede}>
      {coupon.providers.length === 0 ? (
        <Empty>This server names no provider a key could be held for.</Empty>
      ) : (
        <ul className="account-keys">
          {coupon.providers.map((provider) => (
            <KeyRow
              key={provider}
              provider={provider}
              held={held.get(provider) ?? null}
              busy={busy}
              onSave={(secret) => save(provider, secret)}
              onRemove={() => remove(provider)}
            />
          ))}
        </ul>
      )}
      <FailureLine failure={cmd.failure} />
      <p className="account-note">
        A key is kept on this server and sent only to its provider, which judges it at the first
        send. Only its last four characters are ever shown again.
      </p>
    </AccountSection>
  );
}

function KeyRow({
  provider,
  held,
  busy,
  onSave,
  onRemove,
}: {
  provider: string;
  held: AccountKey | null;
  busy: boolean;
  onSave: (secret: string) => void;
  onRemove: () => void;
}) {
  return (
    <li className="account-key">
      <span className="account-key-provider">{provider}</span>
      {held !== null ? (
        <>
          <span className="account-key-held">
            <code aria-label={`${provider} key ending ${held.last4}`}>…{held.last4}</code>
            <span className="account-note">added {fmtAgo(held.added_at)}</span>
          </span>
          <Button
            variant="danger"
            disabled={busy}
            aria-label={`Remove the ${provider} key`}
            onClick={onRemove}
          >
            Remove
          </Button>
        </>
      ) : (
        <CommitInput
          className="account-key-input"
          type="password"
          value=""
          disabled={busy}
          placeholder="Paste a key, then Enter"
          aria-label={`${provider} API key`}
          validate={(draft) => draft !== ""}
          onCommit={onSave}
        />
      )}
    </li>
  );
}
