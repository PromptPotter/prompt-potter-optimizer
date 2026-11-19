import { useEffect, useState } from "react";
import { AccountSection } from "./AccountSection";
import { Button, Chip, ChipGroup, CommitInput, Empty, FailureLine } from "@/components/ui";
import { ACCOUNT, listApiTokens, mintApiToken, revokeApiToken } from "@/lib/api";
import type { AccountTokens, MintedToken, TokenEntry } from "@/lib/api/types";
import { fmtAgo } from "@/lib/format";
import { useCommand } from "@/lib/hooks/useCommand";
import { useFiles } from "@/lib/hooks/useFiles";

type TokenVerb = "list" | "mint" | "revoke";

// The listing is a command, re-run after each mint and revoke; a reader who may not list reads none.
export function ApiTokens() {
  const files = useFiles("account");
  const lists = files("list-api-tokens");
  const cmd = useCommand<TokenVerb>("account-tokens", ACCOUNT);
  const [held, setHeld] = useState<AccountTokens | null>(null);
  const [minted, setMinted] = useState<MintedToken | null>(null);
  const [scopes, setScopes] = useState<ReadonlySet<string> | null>(null);
  const busy = cmd.pending !== null;

  const relist = () => void cmd.run("list", listApiTokens, setHeld);
  useEffect(() => {
    if (lists) relist();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once its reader lists
  }, [lists]);

  const chosen = scopes ?? new Set(held?.grantable ?? []);
  const toggle = (scope: string) =>
    setScopes(chosen.has(scope) ? new Set([...chosen].filter((s) => s !== scope)) : new Set([...chosen, scope]));
  // The relist goes after the send settles: `run` refuses a send while one is in flight.
  const mint = (name: string) =>
    void cmd.run("mint", () => mintApiToken(name, [...chosen]), setMinted).then((r) => r.ok && relist());
  const revoke = (token: TokenEntry) =>
    void cmd.run("revoke", () => revokeApiToken(token.id)).then((r) => r.ok && relist());

  if (!lists) return null;
  return (
    <AccountSection
      title="Your API tokens"
      lede="A token lets a program act as your account over the API, within the scopes you give it."
    >
      {minted !== null && (
        <p className="account-token-minted" role="status">
          <span>
            Copy <strong>{minted.token.name}</strong> now: it is shown this once.
          </span>
          <code aria-label="The new token">{minted.secret}</code>
        </p>
      )}
      {held !== null && held.grantable.length > 0 && files("mint-api-token") && (
        <div className="account-token-mint">
          <ChipGroup label="Scopes">
            {held.grantable.map((scope) => (
              <Chip key={scope} on={chosen.has(scope)} disabled={busy} onClick={() => toggle(scope)}>
                {scope}
              </Chip>
            ))}
          </ChipGroup>
          <CommitInput
            key={minted?.token.id}
            className="account-key-input"
            value=""
            disabled={busy || chosen.size === 0}
            placeholder="Name a new token, then Enter"
            aria-label="New token name"
            validate={(draft) => draft.trim() !== ""}
            onCommit={(name) => mint(name.trim())}
          />
        </div>
      )}
      {held !== null &&
        (held.tokens.length === 0 ? (
          <Empty>No token yet.</Empty>
        ) : (
          <ul className="account-keys">
            {held.tokens.map((token) => (
              <li key={token.id} className="account-key">
                <span className="account-key-provider">{token.name}</span>
                <span className="account-key-held">
                  <span>{token.scopes.join(", ")}</span>
                  <span className="account-note">
                    {token.revoked_at === null
                      ? `minted ${fmtAgo(token.created_at)}`
                      : `revoked ${fmtAgo(token.revoked_at)}`}
                  </span>
                </span>
                {token.revoked_at === null && files("revoke-api-token") && (
                  <Button
                    variant="danger"
                    disabled={busy}
                    aria-label={`Revoke the ${token.name} token`}
                    onClick={() => revoke(token)}
                  >
                    Revoke
                  </Button>
                )}
              </li>
            ))}
          </ul>
        ))}
      <FailureLine failure={cmd.failure} />
    </AccountSection>
  );
}
