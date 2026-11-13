import { useState, type ReactNode } from "react";
import { useWorkspace } from "@/lib/workspace";
import { useRound } from "@/lib/hooks/useRound";
import { nodeSchemaReading } from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import { ARM_END_INFO } from "@/lib/api/types";
import type { CellsPreset } from "@/lib/hooks/useCells";
import { useViewedDashboard } from "@/lib/poll";
import type { ArmRow } from "@/lib/types";
import type { CandidateVerdict } from "@/lib/derivations";
import { MeasurementsPane } from "@/components/measurements/MeasurementsPane";
import { fmtUnit, unitPlural } from "@/lib/format";
import {
  Badge,
  CopyButton,
  Empty,
  FailureLine,
  Loading,
  SegmentedControl,
  Term,
  type Segment,
} from "@/components/ui";
import { NodeSurface } from "./NodeSurface";

export function MeasurementRun({
  round,
}: {
  round: number;
}) {
  const { dash, isLive } = useViewedDashboard();
  const read = useConnector();
  const schema = nodeSchemaReading(read);
  const { viewed, pick, pickCandidate } = useWorkspace();
  const viewedPath = viewed?.path ?? null;
  const onSelectCandidate = (c: ArmRow | null): void => {
    const arm = c?.reading.arm;
    pickCandidate(arm ? { round: arm.round, arm: arm.key } : null);
  };
  const {
    unfiled: isLiveView,
    read: roundRead,
    rows: candidates,
    seed,
    verdicts,
  } = useRound(viewedPath, round);

  const [candFilter, setCandFilter] = useState<string>("all");
  const picked = candidates.find((c) => c.reading.arm.key === candFilter);
  const narrowTo = picked && picked.source !== "inflight" ? picked.reading.arm.key : undefined;
  const preset: CellsPreset = { round, scope: "cycle", candidateId: narrowTo };
  const oneCandidate = candFilter !== "all";
  const shown = oneCandidate
    ? candidates.filter((c) => c.reading.arm.key === candFilter)
    : candidates;

  if (!dash || (!isLiveView && roundRead.status === "loading")) {
    return (
      <Region>
        <Loading className="samples-empty">Loading round {round}…</Loading>
      </Region>
    );
  }
  const unit = dash.measured_unit;
  if (!isLiveView && roundRead.status === "failed") {
    return (
      <Region>
        <FailureLine
          failure={roundRead.failure}
          lead={`Could not load round ${round}.`}
          className="samples-empty"
        />
      </Region>
    );
  }
  if (candidates.length === 0) {
    return (
      <Region>
        <Empty className="samples-empty">
          {isLiveView && isLive
            ? "No candidates running yet this round. They'll appear here as the optimizer scores them."
            : `Round ${round} carries no candidates.`}
        </Empty>
      </Region>
    );
  }

  return (
    <section className="opt-detail-samples" aria-label="What this step scored">
      <div className="rsv-filters">
        <div className="rsv-cand-strip">
          <SegmentedControl<string>
            options={[
              { value: "all", label: `ALL (${candidates.length})` },
              ...candidates.map((c) => segmentFor(c, verdicts.get(c.reading.arm.key))),
            ]}
            value={candFilter}
            onChange={setCandFilter}
            ariaLabel="Which candidate's rows to show"
          />
        </div>
      </div>
      <div className="rsv-groups">
        {shown.map((c) => {
          const { reading } = c;
          const { arm, end } = reading;
          const spec = oneCandidate ? seed(arm.key) : null;
          const invalid = end === "invalid";
          // An invalid arm never started a walk: it renders as rejected below, never as a stop.
          const early = reading.walked_short ? end : null;
          const fitness = reading.own?.fitness?.value ?? null;
          const isCandSelected = pick?.round === arm.round && pick.arm === arm.key;
          const { cached, cached_share: cachedShare } = reading.panel;
          const verdict = verdicts.get(arm.key);
          return (
            <section
              key={c.key}
              className={`rsv-group${isCandSelected ? " selected" : ""}`}
            >
              <button
                type="button"
                className="rsv-group-head"
                onClick={() => onSelectCandidate(isCandSelected ? null : c)}
                title="Click to anchor lineage + fitness on this candidate"
              >
                <span className="rsv-cand-label">{arm.label}</span>
                <span className="rsv-tally">
                  {invalid ? (
                    <Badge tone="danger">
                      <Term content="Rejected by validation — it never ran, so the scores served beside it are synthetic.">
                        rejected
                      </Term>
                    </Badge>
                  ) : (
                    <span className="rsv-tally-score">
                      {[
                        reading.panel.scored != null ? `${reading.panel.scored} scored` : null,
                        fitness != null ? fmtUnit("score", fitness) : null,
                      ]
                        .filter((part) => part !== null)
                        .join(" · ")}
                    </span>
                  )}
                  {early && (
                    <Badge tone={ARM_END_INFO[early].tone}>
                      <Term content={ARM_END_INFO[early].sentence}>
                        {ARM_END_INFO[early].label}
                        {reading.refused_parameter ? ` ${reading.refused_parameter}` : null}
                      </Term>
                    </Badge>
                  )}
                  {cached != null && cached > 0 && (
                    <Term
                      className="tag-cached"
                      content={`Reused ${unitPlural(unit)} from a prior identical searchpoint — no fresh backend call`}
                    >
                      📖 {cachedShare === 1 ? "all cached" : `${cached} cached`}
                    </Term>
                  )}
                </span>
              </button>
              {verdict && (verdict.changes !== "" || verdict.failures.length > 0) && (
                <div className="rsv-why">
                  {verdict.changes !== "" && (
                    <p className="rsv-changes" title={verdict.changes}>
                      {verdict.changes}
                    </p>
                  )}
                  {verdict.failures.map((f, i) => (
                    <p
                      key={`${f.reason}-${i}`}
                      className="rsv-reject-reason"
                      title={
                        f.allowed.length > 0
                          ? `${f.axis} — wanted ${f.allowed.join(", ")}`
                          : f.axis
                      }
                    >
                      {f.value}
                    </p>
                  ))}
                </div>
              )}
              {spec && (
                <div className="rsv-spec-row">
                  <details className="rsv-spec">
                    <summary>What {arm.label} ran</summary>
                    <NodeSurface
                      node={null}
                      point={spec}
                      overlay={spec.pipeline_overlay}
                      schema={schema}
                      mode="values"
                      compact
                    />
                  </details>
                  <CopyButton
                    data={{
                      label: arm.label,
                      resolved_params: spec.pipeline_overlay,
                      prompts: spec.origin_prompts,
                    }}
                    title={`Copy what ${arm.label} ran`}
                  />
                </div>
              )}
            </section>
          );
        })}
      </div>
      <MeasurementsPane preset={{ ...preset, groupBy: "candidate" }} />
    </section>
  );
}

function Region({ children }: { children: ReactNode }) {
  return (
    <section className="opt-detail-samples" aria-label="What this step scored">
      {children}
    </section>
  );
}

// `rejected`, never `0%`: an invalid arm holds no level.
function segmentFor(c: ArmRow, verdict: CandidateVerdict | undefined): Segment<string> {
  const { arm, own } = c.reading;
  if (c.reading.end === "invalid") {
    const reason = verdict?.failures[0]?.value;
    return {
      value: arm.key,
      label: (
        <>
          {arm.label} · <span className="rsv-rejected">rejected</span>
        </>
      ),
      ariaLabel: `${arm.label}, rejected`,
      hint: reason ?? "Rejected by validation before it ran.",
    };
  }
  const fitness = own?.fitness?.value;
  return {
    value: arm.key,
    label: fitness != null ? `${arm.label} · ${fmtUnit("score", fitness)}` : arm.label,
  };
}
