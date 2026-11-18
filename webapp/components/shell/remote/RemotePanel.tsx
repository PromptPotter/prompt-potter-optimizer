import { Term } from "@/components/ui";
import { RunLimitsControl } from "@/components/shell/remote/RunLimitsControl";
import { SamplesInFlight } from "./SamplesInFlight";
import type { RemoteModel } from "./useRemoteModel";

// The panel says what the masthead does not: whether an operator's hand is on this cycle.
function Provenance() {
  return (
    <div className="remote-panel-section">
      <div className="section-title">Provenance</div>
      <div className="row">
        <span className="lbl">Intervened</span>
        <Term className="val remote-babysat" content="An operator manually intervened (skip) — this cycle is no longer purely reproducible.">
          <span aria-hidden="true">✎</span> babysat
        </Term>
      </div>
    </div>
  );
}

export function RemotePanel({ model: m }: { model: RemoteModel }) {
  return (
    <div className="remote-panel" role="region" aria-label="Job status and configuration">
      {m.babysat && <Provenance />}
      <SamplesInFlight model={m} />
      <div className="remote-panel-section">
        <div className="section-title">
          Finishing criteria <span className="section-aside">whichever trips first</span>
        </div>
        <RunLimitsControl caps={m.caps} overshoot={m.overshoot} overshot={m.overshot} />
      </div>
    </div>
  );
}
