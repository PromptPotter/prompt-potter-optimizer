"use client";
import { useCycleStream } from "@/lib/poll";
import { useObserveSubject } from "@/lib/hooks/useObserveSubject";
import { useOrigin } from "@/lib/hooks/useOrigin";
import { useOriginEvidence } from "@/lib/hooks/useOriginEvidence";
import { useSearchpointRead } from "@/lib/hooks/useSearchpointRead";
import { useSelection } from "@/lib/SelectionContext";
import {
  readSpend,
  searchpointCopyChoices,
  searchpointText,
  type ObserveState,
} from "@/lib/derivations";
import { CopyButton, SegmentedControl } from "@/components/ui";
import { RoundFacts, RoundVerdict } from "./RoundFacts";
import { SubjectDiff } from "./SubjectDiff";
import { SubjectFlips } from "./SubjectFlips";
import { SubjectHeadline } from "./SubjectHeadline";
import { SubjectSignals } from "./SubjectSignals";
import { WinnerStanding } from "./WinnerStanding";

export function SubjectBox() {
  const { dash } = useCycleStream();
  const { setObserve } = useSelection();
  const subject = useObserveSubject();
  const { point, reading } = subject;
  const shown = useSearchpointRead(point);
  const origin = useOrigin();
  const originRead = useSearchpointRead(origin);

  const cfg = shown.cfg;
  const evidence = useOriginEvidence(point);

  if (!dash?.cycle_id) return null;
  const row = point?.row ?? null;
  return (
    <div className="subject-box">
      <SubjectSignals origin={evidence.origin} shown={evidence.shown} compare={evidence.compare} />
      <div className="subject-head">
        <SubjectHeadline
          metered={readSpend(dash).metered}
          reading={row?.reading ?? null}
          bench={subject.state === "best" ? (dash.bench_score?.vs_origin ?? null) : null}
        />
        {subject.options.length > 1 ? (
          <SegmentedControl<ObserveState>
            options={subject.options}
            value={subject.state}
            onChange={setObserve}
            ariaLabel="Which searchpoint to show"
          />
        ) : null}
        <CopyButton
          choices={[
            ...searchpointCopyChoices({ cfg, reading: row?.reading }),
            ...(cfg
              ? [{ key: "text", label: "Prompt + config as text", data: searchpointText(cfg) }]
              : []),
          ]}
          title="Copy this searchpoint"
        />
      </div>
      {subject.state === "best" && dash.run_standing ? (
        <WinnerStanding standing={dash.run_standing} />
      ) : null}
      <RoundFacts reading={reading} />
      {cfg ? (
        <SubjectDiff
          cfg={cfg}
          changes={evidence.changes}
          verdict={<RoundVerdict reading={subject.verdict} />}
        />
      ) : (
        <p className="subject-note">
          {shown.loading
            ? "Loading the searchpoint…"
            : shown.failure
              ? shown.failure.message
              : "Nothing measured yet."}
        </p>
      )}
      <SubjectFlips
        vsOrigin={evidence.vsOrigin}
        partial={
          evidence.shown?.status === "minted"
            ? { scored: evidence.shown.n_cells, expected: evidence.shown.expected_samples }
            : null
        }
        before={originRead.samples} after={shown.samples} />
    </div>
  );
}
