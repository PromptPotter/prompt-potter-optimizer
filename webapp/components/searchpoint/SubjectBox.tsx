import { useViewedCycle } from "@/lib/state/lineage";
import { useViewedDashboard } from "@/lib/state/poll";
import { useObserveSubject } from "@/lib/hooks/useObserveSubject";
import { useOrigin } from "@/lib/hooks/useOrigin";
import { useOriginEvidence } from "@/lib/hooks/useOriginEvidence";
import { useSearchpointRead } from "@/lib/hooks/useSearchpointRead";
import { useSelection } from "@/lib/state/selection";
import { useSearchpointCopy } from "@/lib/hooks/useSearchpointCopy";
import { useWorkspace } from "@/lib/state/workspace";
import type { ObserveState } from "@/lib/address";
import { readGap } from "@/lib/hooks/useRead";
import { CopyButton, Empty, FailureLine, Loading, ReadState, SegmentedControl } from "@/components/ui";
import { RoundFacts, RoundVerdict } from "./RoundFacts";
import { SubjectDiff } from "./SubjectDiff";
import { SubjectFlips } from "./SubjectFlips";
import { SubjectHeadline } from "./SubjectHeadline";
import { SubjectSignals } from "./SubjectSignals";
import { WinnerStanding } from "./WinnerStanding";

export function SubjectBox() {
  const { read } = useViewedDashboard();
  const { setObserve } = useSelection();
  const subject = useObserveSubject();
  const { point, reading } = subject;
  const shown = useSearchpointRead(point);
  const origin = useOrigin();
  const cycle = useViewedCycle();
  const standing = cycle?.facts.standing ?? null;

  const cfg = shown.cfg;
  const evidence = useOriginEvidence(point);
  const { viewed } = useWorkspace();
  const copy = useSearchpointCopy({
    path: viewed?.path ?? null,
    round: point?.round ?? null,
    arm: point?.key ?? null,
    label: point?.title ?? "",
    datasetName: null,
  });

  // I1: until the viewed cycle's dashboard lands, the card says why it shows no leader.
  const gap = readGap(read);
  if (gap) return <ReadState read={gap} subject="the viewed run's leader">{() => null}</ReadState>;
  return (
    <div className="subject-box">
      <SubjectSignals origin={evidence.origin} shown={evidence.shown} compare={evidence.compare} />
      <div className="subject-head">
        <SubjectHeadline reading={shown.reading} />
        {subject.options.length > 1 ? (
          <SegmentedControl<ObserveState>
            options={subject.options}
            value={subject.state}
            onChange={setObserve}
            ariaLabel="Which searchpoint to show"
          />
        ) : null}
        <CopyButton choices={copy.choices} title="Copy this searchpoint" />
      </div>
      {subject.state === "best" && standing ? (
        <WinnerStanding standing={standing} />
      ) : null}
      <RoundFacts reading={reading} />
      {cfg ? (
        <SubjectDiff
          cfg={cfg}
          changes={evidence.changes}
          read={evidence.read}
          verdict={<RoundVerdict reading={reading} />}
        />
      ) : shown.read.status === "failed" ? (
        <FailureLine failure={shown.read.failure} />
      ) : shown.read.status === "loading" ? (
        <Loading>Loading the searchpoint…</Loading>
      ) : (
        <Empty>Nothing measured yet.</Empty>
      )}
      <SubjectFlips
        vsOrigin={evidence.vsOrigin}
        partial={
          evidence.shown?.status === "proposed" || evidence.shown?.status === "measuring"
            ? { scored: evidence.shown.n_cells, expected: evidence.shown.expected_samples }
            : null
        }
        before={origin}
        after={point}
      />
    </div>
  );
}
