import { Term } from "@/components/ui";
import { STOP_REASON_INFO } from "@/lib/api/types.generated";
import { useCycleStream } from "@/lib/poll";

export function RunErrorBanner() {
  const { dash } = useCycleStream();
  const err = dash?.error;
  const warnings = (dash?.recent_loop_warnings ?? []).slice(-4).reverse();

  if (!err && warnings.length === 0) return null;

  const lines = err ? err.message.split("\n").filter((l) => l.trim().length > 0) : [];

  return (
    <>
      {err ? (
        <div className="run-error-banner" role="alert">
          <div className="run-error-banner-head">
            <span className="run-error-banner-kind">{err.kind}</span>
            <Term
              className="run-error-banner-stop"
              content={STOP_REASON_INFO[err.stop_reason].next_step || undefined}
            >
              {STOP_REASON_INFO[err.stop_reason].label}
            </Term>
          </div>
          <p className="run-error-banner-msg">{lines[0] ?? err.message}</p>
          {lines.length > 1 ? (
            <ul className="run-error-banner-list">
              {lines.slice(1).map((l, i) => (
                <li key={i}>{l}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {warnings.length > 0 ? (
        <div className="run-error-banner is-warn" role="status">
          <div className="run-error-banner-head">
            <span className="run-error-banner-kind">Optimizer warnings</span>
          </div>
          <ul className="run-error-banner-list">
            {warnings.map((w, i) => (
              <li key={i}>
                {typeof w.round === "number" ? `r${w.round} · ` : ""}
                {w.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </>
  );
}
