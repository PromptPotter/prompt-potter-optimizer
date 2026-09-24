"use client";
// Where the scoring mask becomes the run: a fork minted at the round the served overlay says the
// readings part. Only a criterion or subset previews; a knob, model or prompt goes to `SteerForkPanel`.

import { useState } from "react";
import { postSteerFork } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import { steeredBy, useAuth } from "@/lib/auth-context";

export function ApplyScenarioPanel({
  campaignId,
  cycleId,
  isLive,
  // `null` when there is nothing to apply, so the panel is not on screen.
  criterion,
  // `null` where the two readings never part.
  divergentRound,
  // Used when nothing diverges: every measured round is kept.
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
          // No `origin_prompt_fields`: the lifted round 0 IS the origin, and the server refuses the pair.
          seed: { config_overrides: { scoring: criterion } },
          steeredBy: steeredBy(me),
          keepRounds: true,
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
