import { THETA_CAVEAT_INFO, type AbilityReading } from "@/lib/api/types";
import type { ThetaCaveat as Caveat } from "@/lib/types";
import { Said } from "@/components/ui";

// Footnotes under the chart, each one served `Said` row; the all-miss one names its arms.
export function ThetaCaveats({
  caveat,
  floorPinned,
}: {
  caveat: Caveat | null;
  floorPinned: readonly string[];
}) {
  return (
    <>
      {caveat && caveat !== "floor_pinned" ? <Said {...THETA_CAVEAT_INFO[caveat]} /> : null}
      {floorPinned.length > 0 ? (
        <Said {...THETA_CAVEAT_INFO.floor_pinned} sub={floorPinned.join(", ")} />
      ) : null}
    </>
  );
}

export function AbilityHelp({
  model,
  caveat,
}: {
  model: AbilityReading["calibration_model"];
  caveat?: AbilityReading["caveat"];
}) {
  return (
    <div className="ability-help">
      <p>
        The round winner is elected on <strong>ability θ</strong>, not raw fitness. θ is
        difficulty-adjusted: clearing a <em>hard</em> sample counts for more than clearing an
        easy one.
      </p>
      <p>
        So a candidate can win with <em>lower</em> fitness when it cleared the harder samples —
        and two candidates scored on different sample subsets still compare fairly, which raw
        fitness can&rsquo;t do. Tap a candidate&rsquo;s bar to read its θ in the detail below
        the chart.
      </p>
      <p className="ability-help-calib">
        {model === "2PL" ? (
          <>
            Calibration: <strong>2PL</strong> — this dataset graduated: θ weighs sample
            difficulty <em>and</em> how much signal each sample carries, so a sample that
            separates good candidates from bad counts for more.
          </>
        ) : model === "1PL" ? (
          <>
            Calibration: <strong>1PL (Rasch)</strong> — difficulty only. (2PL, which also weighs
            how much signal each sample carries, graduates per dataset once the data supports
            it.)
          </>
        ) : (
          <>
            Calibration: <strong>not yet calibrated</strong> — too few banked samples to fit a
            difficulty ruler, so θ is plain fitness on the logit scale. It calibrates itself as
            rounds bank samples.
          </>
        )}
      </p>
      {caveat ? (
        <p className="ability-help-calib">
          <strong>{THETA_CAVEAT_INFO[caveat].label}</strong> {THETA_CAVEAT_INFO[caveat].detail}
        </p>
      ) : null}
    </div>
  );
}
