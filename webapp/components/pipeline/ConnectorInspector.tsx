import { HoverCard, Term } from "@/components/ui";
import { cx } from "@/lib/cx";
import { PLACEHOLDER } from "@/lib/format";
import { connectorReachability, interiorNodes } from "@/lib/derivations";
import { useBackend, useConnector } from "@/lib/hooks/useConnector";
import { shownData } from "@/lib/hooks/useRead";

const SECURITY_DOC_URL =
  "https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/backend-integration.md#connection-security";

export function ConnectorInspector() {
  const read = useConnector();
  const backend = useBackend();
  const served = shownData(read);
  const connector = served?.connector ?? null;
  const selfOpt = served?.self_optimization ?? false;
  // Before the pipeline lands, the other backends are listed only once its read has failed.
  const others = served || read.status === "failed" ? backend.others : [];
  const { health } = backend;
  // Only a remote wire has a connection to secure.
  const remote = backend.active?.execution === "remote_http";
  const unset = remote && !backend.active?.configured;
  const urls = Object.values(backend.active?.endpoints ?? {});
  const authHeader = backend.active?.auth_header ?? null;
  const isTls = urls.length > 0 ? urls.every((url) => url.startsWith("https://")) : null;
  const { reachable, stateCls, stateLabel } = connectorReachability(health);
  // No resolved connector is a terminal "idle", never a perpetual "probing…" (webapp/CLAUDE.md § I1).
  const noBackend = connector == null;
  const label = selfOpt ? "self-optimization" : noBackend ? "idle" : stateLabel;
  // The head names the state once; the foot says only what the label cannot.
  const footText = selfOpt
    ? "L4 self-optimization — backend is PromptPotter itself; the per-node backend lives in the inner run"
    : noBackend
      ? "no backend selected"
      : !health
        ? null
        : reachable
          ? `checked ${new Date(health.checked_at).toLocaleTimeString()}`
          : health.detail;
  const interior = interiorNodes(served?.view ?? null);

  const pop = (
    <div role="group" aria-label="Connector state">
      <div className="connector-pop-head">
        <span className="dot" />
        <span className="name">{connector ?? PLACEHOLDER}</span>
        <span className="state">{label}</span>
      </div>
      {urls.map((url) => (
        <div key={url} className="connector-pop-url" title={url}>
          {url}
        </div>
      ))}
      <div className="connector-pop-sec">
        {unset && (
          <span className="sec-chip warn" title="No address configured: every call refuses">
            no url
          </span>
        )}
        {isTls !== null && (
          <span
            className={`sec-chip ${isTls ? "ok" : "warn"}`}
            title={
              isTls
                ? "https — TLS chain verified by httpx default"
                : "http — cleartext; do not use over a public network"
            }
          >
            {isTls ? "tls" : "plain"}
          </span>
        )}
        {remote && (
          <>
            <span className={cx("sec-chip", !authHeader && "warn")}>
              {authHeader ? `auth: ${authHeader}` : "no auth"}
            </span>
            <a
              className="sec-doc"
              href={SECURITY_DOC_URL}
              target="_blank"
              rel="noopener noreferrer"
              title="Open the connection-security setup guide on GitHub"
            >
              docs ↗
            </a>
          </>
        )}
      </div>
      {interior.length === 0 && (
        <p className="connector-pop-empty">
          {selfOpt
            ? "optimizer prompt nodes — inspect them in the inner run"
            : "no nodes loaded"}
        </p>
      )}
      {others.length > 0 && (
        <div className="connector-pop-switch">
          <div className="head">
            Other backends ·{" "}
            <Term content="Switching is read-only today — see the connection-security docs.">read-only</Term>
          </div>
          <ul>
            {others.map((b) => (
              <li key={b.id}>
                <button
                  type="button"
                  className="alt"
                  disabled
                >
                  <span className="alt-name">{b.id}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {footText ? <div className="connector-pop-foot">{footText}</div> : null}
    </div>
  );

  // The dot is the card's trigger, not a control of its own: a tap opens the card.
  return (
    <div className={cx("connector", stateCls)}>
      <HoverCard content={pop} className={cx("connector-pop", stateCls)} focusable>
        <span
          className="connector-dot"
          role="img"
          aria-label={`Connector ${connector ?? PLACEHOLDER} — ${label}`}
        />
      </HoverCard>
    </div>
  );
}
