"use client";
// Where the scoring mask stops being a preview and becomes the run.
//
// A scoring criterion is one of exactly two settings that can be previewed at all: it and the
// sample subset are RE-PROJECTIONS of the record, so the server re-decides every election from
// rows already measured and can name the round the two readings part. That round is not a
// coincidence — it IS the round a fork applying the criterion is minted at, because everything
// before it is a stretch both readings agree on and everything after stood on a parent the run
// never had. The preview and the action were the same fact computed twice and never connected.
//
// Everything else an operator might change mid-run — a node knob, a model, a prompt — has no
// measurement to be re-read under, so it previews nothing and goes straight to a fork
// (`SteerForkPanel`). Two settings alone move a RUNNING cycle in place, budget and sample
// look-ahead, and neither is a scenario. That boundary is stated here, where the operator is
// choosing, rather than in a document.

import { useState } from "react";
import { postSteerFork } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import { steeredBy, useAuth } from "@/lib/auth-context";

export function ApplyScenarioPanel({
  campaignId,
  cycleId,
  isLive,
  // The bare formula `CampaignConfig.scoring` takes (`scoring-mask.ts::criterionOf`). `null` when the
  // mask is off, has no terms, or carries a lens in a namespace no config expresses — each of
  // which is "there is nothing here to apply", so the panel simply is not on screen.
  criterion,
  // The round the served overlay says the two readings part at, or `null` where they never do.
  divergentRound,
  // The round the branch would continue at. Used when nothing diverges: applying the criterion
  // then keeps every measured round and only changes what happens next.
  nextRound,
}: {
  campaignId: string | null;
  cycleId: string | null;
  isLive: boolean;
  criterion: string | null;
  divergentRound: number | null;
  nextRound: number;
}) {
  const { me } = useAuth();
  const cmd = useCommand<"apply-scenario">("apply-scenario");
  const [done, setDone] = useState(false);

  if (!criterion || !campaignId || !cycleId) return null;
  const at = divergentRound ?? nextRound;
  const pending = cmd.pending !== null;

  const apply = () =>
    void cmd.run(
      "apply-scenario",
      () =>
        postSteerFork(campaignId, cycleId, at, "", {
          // No `origin_prompt_fields`: rounds 0..at-1 are lifted and their round 0 IS the
          // origin. The server refuses the pair, so this is the shape rather than a convention.
          seed: { config_overrides: { scoring: criterion } },
          steeredBy: steeredBy(me),
          keepRounds: true,
          // This supersedes the parent: the line moves.
          pauseFirst: isLive,
        }),
      () => setDone(true),
    );

  return (
    <div className="mask-apply">
      <p className="l4-lede">
        {divergentRound !== null ? (
          <>
            Under this criterion the record and the counterfactual <strong>part at round{" "}
            {divergentRound}</strong> — rounds 0–{divergentRound - 1} are a stretch both readings
            agree on. Applying it keeps those and continues from {divergentRound}. Nothing past
            that point is claimed: the run would have stood on a parent it never had, so there is
            no measurement to read.
          </>
        ) : (
          <>
            This criterion <strong>never parts from the record</strong> — every election it
            re-decides lands on the winner that was crowned. Applying it keeps all{" "}
            {nextRound} measured round{nextRound === 1 ? "" : "s"} and changes only what happens
            from round {nextRound}.
          </>
        )}
      </p>
      <p className="l4-note">
        The criterion and the sample subset are the only two settings a preview can reach: both
        re-read rows already measured. A node parameter, a model or a prompt has no measurement to
        be re-read under, so it forks with no preview — that is the steer panel on a searchpoint.
        Budget and sample look-ahead are the only two that move a running cycle in place.
      </p>
      {cmd.failure && (
        <p className="l4-warn" role="alert">
          apply: {cmd.failure.message}
        </p>
      )}
      {done ? (
        <p className="l4-note">
          Forked at round {at}. The sidebar follows the new branch as it comes up.
        </p>
      ) : (
        <button
          type="button"
          className="cmp-button"
          disabled={pending}
          onClick={apply}
          title={`Mint a branch at round ${at} carrying this criterion, keeping every round before it. Tagged operator_rewind in lineage.`}
        >
          {pending ? "Applying…" : isLive ? `Stop & apply from round ${at}` : `Apply from round ${at}`}
        </button>
      )}
    </div>
  );
}
