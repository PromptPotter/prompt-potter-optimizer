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
  const backendType = served?.backend_type ?? null;
  const selfOpt = served?.self_optimization ?? false;
  // Before the pipeline lands, the other backends are listed only once its read has failed.
  const others = served || read.status === "failed" ? backend.others : [];
  const { health } = backend;
  const baseUrl = backend.active?.base_url ?? null;
  const isTls = baseUrl ? baseUrl.startsWith("https://") : null;
  const { reachable, stateCls, stateLabel } = connectorReachability(health);
  // No resolved connector is a terminal "idle", never a perpetual "probing…" (frontend-surface-contract.md § I1).
  const noBackend = connector == null && !selfOpt;
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

  // A div host, not a span: the popover below is flow content, which no inline element may hold.
  return (
    <div className={cx("connector", stateCls)}>
      <button
        type="button"
        className="connector-dot"
        aria-label={`Connector ${connector ?? (selfOpt ? "PromptPotter" : PLACEHOLDER)} — ${label}`}
      />
      <div className="connector-pop" role="group" aria-label="Connector state">
        <div className="connector-pop-head">
          <span className="dot" />
          <span className="name">{connector ?? (selfOpt ? "PromptPotter" : PLACEHOLDER)}</span>
          {backendType && <span className="kind">{backendType}</span>}
          <span className="state">{label}</span>
        </div>
        {baseUrl && (
          <div className="connector-pop-url" title={baseUrl}>
            {baseUrl}
          </div>
        )}
        <div className="connector-pop-sec">
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
          <span
            className="sec-chip"
            title="Authorization: Bearer <TERMNORM_TOKEN> on every request when the env var is set"
          >
            auth: bearer
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
            <div className="head">Other backends</div>
            <ul>
              {others.map((b) => (
                <li key={b.id}>
                  <button
                    type="button"
                    className="alt"
                    disabled
                    title="Switching is read-only today — see connection-security docs"
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
    </div>
  );
}
