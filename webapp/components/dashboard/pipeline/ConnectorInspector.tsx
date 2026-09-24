// Connector-state inspector: dot button + popover over the Input→LLM arrow. Presentational —
// `ConnectorProvider` owns the join, so this never fetches, derives or matches by string.

import { cx } from "@/lib/cx";
import { connectorReachability, interiorNodes, isSelfOptimization } from "@/lib/derivations";
import type { ConnectorView } from "@/lib/types";

const SECURITY_DOC_URL =
  "https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/backend-integration.md#connection-security";

interface Props {
  view: ConnectorView;
}

export function ConnectorInspector({ view }: Props) {
  const {
    connector,
    backendType,
    view: pipelineView,
    others,
    baseUrl,
    isTls,
    currentNodes,
    health,
  } = view;
  // Shared with CriticalAlertBanner (lib/derivations/connector-state.ts), so LED and banner agree.
  const { reachable, stateCls, stateLabel } = connectorReachability(health);
  // An L4 unit's backend is PromptPotter itself, with no HTTP backend to probe; the real one lives
  // in the inner run.
  const selfOpt = isSelfOptimization(backendType);
  // No resolved connector means nothing is probed: a terminal "idle", never a perpetual
  // "probing…" (frontend-surface-contract.md § I1).
  const noBackend = connector == null && !selfOpt;
  const label = selfOpt ? "self-optimization" : noBackend ? "idle" : stateLabel;
  const footText = selfOpt
    ? "L4 self-optimization — backend is PromptPotter itself; the per-node backend lives in the inner run"
    : noBackend
      ? "no backend selected"
      : !health
        ? "probe pending…"
        : reachable
          ? `reachable · ${new Date(health.checked_at).toLocaleTimeString()}`
          : `unreachable${health.detail ? ` · ${health.detail}` : ""}`;
  const interior = interiorNodes(pipelineView);

  // A div host, not a span: the popover below is flow content, which no inline element may hold.
  return (
    <div className={cx("connector", stateCls)}>
      <button
        type="button"
        className="connector-dot"
        aria-label={`Connector ${connector ?? (selfOpt ? "PromptPotter" : "—")} — ${label}`}
      />
      <div className="connector-pop" role="group" aria-label="Connector state">
        <div className="connector-pop-head">
          <span className="dot" />
          <span className="name">{connector ?? (selfOpt ? "PromptPotter" : "—")}</span>
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
        {/* No node roster: the graph in the same row draws them from the same `view`. */}
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
              {others.map((b) => {
                const sameLabel = b.name.toLowerCase() === b.backend_type.toLowerCase();
                return (
                  <li key={b.id}>
                    <button
                      type="button"
                      className="alt"
                      disabled
                      title="Switching is read-only today — see connection-security docs"
                    >
                      <span className="alt-name">{b.name}</span>
                      {!sameLabel && <span className="alt-type">{b.backend_type}</span>}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
        <div className="connector-pop-foot">{footText}</div>
      </div>
    </div>
  );
}
