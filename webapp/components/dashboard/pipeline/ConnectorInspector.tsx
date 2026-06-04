// Connector-state inspector — small dot button + hover popover sitting
// over the Input→LLM arrow. Presentational; receives one `view:
// ConnectorView` prop. The data join (registered backends + dataset
// overlay + dashboard live state) is owned by the `ConnectorProvider` so this
// component never fetches, derives, or matches by string.
//
// Mother object on the Python side: `BackendConnection`
// (`promptpotter/domain/backend.py`) — `{id, name, backend_type,
// base_url, created_at}`. The popover header surfaces name/type/state,
// the security row surfaces TLS scheme + auth contract + a link to the
// canonical setup doc on GitHub, and the "Other backends" section lists
// registered alternatives (switching is read-only today; the M12
// control-plane wires the write half).

import { cx } from "@/lib/cx";
import { connectorReachability } from "@/lib/derivations/connector-state";
import type { ConnectorView } from "@/lib/types/connector";

const SECURITY_DOC_URL =
  "https://github.com/runfish5/prompt-potter-optimizer/blob/main/docs/operations/backend-integration.md#connection-security";

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
  // Reachability verdict — shared with the CriticalAlertBanner so the LED and
  // the top banner can never disagree (lib/derivations/connector-state.ts).
  const { reachable, stateCls, stateLabel } = connectorReachability(health);
  // No resolved connector (anon preview / no dataset selected) means nothing is
  // being probed — show a terminal "idle", not a perpetual "probing…" that
  // never resolves (frontend-surface-contract.md § I1).
  const noBackend = connector == null;
  const label = noBackend ? "idle" : stateLabel;
  const footText = noBackend
    ? "no backend selected"
    : !health
      ? "probe pending…"
      : reachable
        ? `reachable · ${new Date(health.checked_at).toLocaleTimeString()}`
        : `unreachable${health.detail ? ` · ${health.detail}` : ""}`;
  const interior = pipelineView ? pipelineView.nodes.filter((n) => n.kind !== "io") : [];

  return (
    <span className={cx("connector", stateCls)}>
      <button
        type="button"
        className="connector-dot"
        aria-label={`Connector ${connector ?? "—"} — ${label}`}
      />
      <div className="connector-pop" role="group" aria-label="Connector state">
        <div className="connector-pop-head">
          <span className="dot" />
          <span className="name">{connector ?? "—"}</span>
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
        <ul className="connector-pop-nodes">
          {interior.length === 0 && <li className="empty">no nodes loaded</li>}
          {interior.map((n) => {
            const model = n.kind === "llm" ? currentNodes[n.id]?.model : null;
            return (
              <li key={n.id}>
                <span className={`kind kind-${n.kind ?? "tool"}`}>{n.kind ?? "tool"}</span>
                <span className="lbl">{n.label}</span>
                {model && <span className="model">{model}</span>}
              </li>
            );
          })}
        </ul>
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
    </span>
  );
}
