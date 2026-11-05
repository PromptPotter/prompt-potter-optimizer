import { AccountSection } from "./AccountSection";
import { Button, CommitInput, Empty, FailureLine, ReadState } from "@/components/ui";
import { couponRead, deleteApiKey, putApiKey } from "@/lib/api";
import {
  type AccountKey,
  type CouponStatus,
  type ProviderRoute,
  KEY_SOURCE_LABELS,
} from "@/lib/api/types";
import { fmtAgo } from "@/lib/format";
import { useCommand } from "@/lib/hooks/useCommand";
import { useRead } from "@/lib/hooks/useRead";

export function AccountKeysTab() {
  const read = useRead(couponRead());
  return (
    <ReadState read={read} subject="your provider keys">
      {(coupon) => <ProviderKeys coupon={coupon} />}
    </ReadState>
  );
}

type KeyVerb = "save-key" | "remove-key";

export function ProviderKeys({ coupon }: { coupon: CouponStatus }) {
  const cmd = useCommand<KeyVerb>("account-keys", { revalidate: ["coupon"] });
  const busy = cmd.pending !== null;

  const save = (provider: string, secret: string) =>
    void cmd.run("save-key", () => putApiKey(provider, secret));
  const remove = (provider: string) => void cmd.run("remove-key", () => deleteApiKey(provider));

  return (
    <AccountSection
      title="Your provider keys"
      lede="A key you hold pays every send on its provider, and draws nothing from the host's allowance."
    >
      {coupon.routes.length === 0 ? (
        <Empty>This server names no provider a key could be held for.</Empty>
      ) : (
        <ul className="account-keys">
          {coupon.routes.map((route) => (
            <KeyRow
              key={route.provider}
              provider={route.provider}
              payer={route.payer}
              held={route.key}
              busy={busy}
              onSave={(secret) => save(route.provider, secret)}
              onRemove={() => remove(route.provider)}
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
  payer,
  held,
  busy,
  onSave,
  onRemove,
}: {
  provider: string;
  payer: ProviderRoute["payer"];
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
        <>
          <span className="account-note">
            {payer === null ? "No key: nothing sends on it." : `Sends on the ${KEY_SOURCE_LABELS[payer]}.`}
          </span>
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
        </>
      )}
    </li>
  );
}
