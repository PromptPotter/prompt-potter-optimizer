import { SteerForkAction } from "@/components/searchpoint/SteerForkAction";
import { VerifyAction } from "@/components/searchpoint/VerifyAction";
import { SearchpointDrillIn } from "@/components/searchpoint/SearchpointDrillIn";
import { SubjectSignals } from "@/components/searchpoint/SubjectSignals";
import { MeasurementsPane } from "@/components/measurements/MeasurementsPane";
import { COPY_WITHOUT_CELLS, selectedNodeOf } from "@/lib/derivations";
import { fmtUnit, PLACEHOLDER, shortId } from "@/lib/format";
import { failureOf } from "@/lib/hooks/useRead";
import { useSearchpointCopy } from "@/lib/hooks/useSearchpointCopy";
import { CopyButton, FailureLine, Said, SegmentedControl, Term } from "@/components/ui";
import { ChannelMap } from "./ChannelMap";
import { MainLine } from "./channel-parts";
import { ChannelRestore, useScenarioEdits, withOverlay } from "./config-edit";
import {
  channelLevel,
  rulerNoteOf,
  useChannelHead,
  useChannelTree,
  useInvalidated,
  usePickedPoint,
} from "./channel-hooks";
import { useChannelView } from "@/lib/state/compare-selection";
import type { ChannelProps } from "./ChannelCard";

// Mounted only while the detail fold is open, so its reads cost nothing until then.
export function ChannelDetail({ item, ownNote, points, spec, run }: ChannelProps) {
  const { reading, headline } = item;
  const axis = spec.axis_label;
  const { edits, setEdits } = useScenarioEdits();
  const { index } = useChannelTree(run);
  const { arms, selected, select, folds, toggleFold } = useChannelView(item, index);
  const head = useChannelHead(arms, selected);
  const picked = usePickedPoint(item, selected, index);
  const { spentTo, line } = picked;
  const copy = useSearchpointCopy({
    path: picked.path,
    round: selected?.facts.reading.arm.round ?? null,
    arm: selected?.facts.reading.arm.key ?? null,
    label: selected?.label ?? "",
    datasetName: reading?.dataset_name ?? null,
  });
  const invalidated = useInvalidated(index);
  const withdrawn = reading !== null && invalidated.has(reading.candidate_id);
  const rulerNote = rulerNoteOf(item, ownNote);
  const level = channelLevel(reading, withdrawn, spec);
  const map = <ChannelMap item={item} run={run} points={points} />;
  if (reading === null) return map;
  if (!level) return null;
  return (
    <>
      {head.options.length > 1 && (
        <SegmentedControl
          options={head.options}
          value={head.value}
          onChange={(v) => select(head.nodeFor(v))}
          ariaLabel="Which searchpoint of this branch is highlighted"
        />
      )}
      <dl className="cmp-channel-facts">
        {headline && (
          <>
            <div>
              <dt>{axis} on search rows</dt>
              <dd>
                {level.value} {level.band}
              </dd>
            </div>
            <div>
              <dt>arm</dt>
              <dd>{headline.purpose.kind === "compare" ? headline.purpose.arm.arm_key : "none"}</dd>
            </div>
            <div>
              <dt>treatment</dt>
              <dd title={headline.treatment_digest}>{headline.treatment_digest.slice(0, 8)}</dd>
            </div>
            <div>
              <dt>bench reads</dt>
              <dd>{headline.bench.status.reads_before ?? PLACEHOLDER}</dd>
            </div>
          </>
        )}
        <div>
          <dt>{spentTo !== null ? `spent to ${selected?.label}` : "branch spend"}</dt>
          <dd>
            {fmtUnit("usd", spentTo ?? reading.cycle_spend_usd)}
          </dd>
        </div>
        <div>
          <dt>dataset</dt>
          <dd title={reading.dataset_name}>{reading.dataset_name || PLACEHOLDER}</dd>
        </div>
        <div>
          <dt>campaign</dt>
          <dd title={reading.campaign_id}>{shortId(reading.campaign_id)}</dd>
        </div>
        {reading.inside.length > 0 && (
          <div>
            <dt>seed of</dt>
            <dd title={reading.inside.map((h) => h.campaign_id).join(" → ")}>
              {shortId(reading.inside[reading.inside.length - 1]?.campaign_id ?? "")}
            </dd>
          </div>
        )}
        <div>
          <dt>branch</dt>
          <dd title={reading.cycle_id}>{shortId(reading.cycle_id)}</dd>
        </div>
        <div>
          <dt>reads at</dt>
          <dd title={reading.candidate_id}>
            {head.own?.label ?? reading.label} · round {reading.round}
          </dd>
        </div>
        <div>
          <dt>rounds on branch</dt>
          <dd>{reading.cycle_rounds_scored}</dd>
        </div>
        <div>
          <dt>authored by</dt>
          <dd title={reading.authorship}>{reading.authorship || PLACEHOLDER}</dd>
        </div>
        <div>
          <dt>replayed cells</dt>
          <dd>{reading.cached_samples ?? PLACEHOLDER}</dd>
        </div>
      </dl>
      {rulerNote && headline && (
        <Said tone="warn" label={reading.comparable_note} />
      )}

      <div className="cmp-channel-line">
        <Term
          className="cmp-metric-label"
          content={
            <p className="cmp-line-key">
              Each round&rsquo;s pick, from the origin up to the highlighted searchpoint: its
              fitness on the cells it measured, then its lift over its parent on those same cells,
              toned by the 95% interval. The lift is the round&rsquo;s verdict — the fitness moves
              with the subset each round drew. A held round crowned nobody, so the line kept its
              parent.
            </p>
          }
        >
          main line
        </Term>
        <MainLine steps={line} />
      </div>

      <p className="cmp-channel-lineage">
        <button
          type="button"
          className="cmp-link"
          aria-expanded={folds.map}
          onClick={() => toggleFold("map")}
        >
          {folds.map ? "▾" : "▸"} Lineage
        </button>
      </p>
      {folds.map && map}

      <div className="cmp-channel-setup-row">
        <details className="cmp-channel-setup" open={folds.setup}>
          <summary
            onClick={(e) => {
              e.preventDefault();
              toggleFold("setup");
            }}
          >
            <span>
              {selected
                ? `${selected.label} · round ${selected.facts.reading.arm.round}`
                : "This searchpoint"}
            </span>
            {!picked.isOwn && (
              <span className="l4-dim">
                {" "}
                — this channel reads at {head.own?.label ?? reading.label}
              </span>
            )}
          </summary>
          <SearchpointDrillIn
            reading={picked.reading}
            cfg={picked.cfg}
            stats={
              picked.isOwn && <SubjectSignals origin={reading} shown={null} compare={null} />
            }
            measurements={
              picked.cellsPreset && (
                <MeasurementsPane preset={{ ...picked.cellsPreset, groupBy: "none" }} />
              )
            }
            pipelineAt={picked.pipelineAt}
            overlay={picked.overlay}
            failure={failureOf(picked.roundRead)}
            pending={
              picked.unfiled
                ? "This round is still scoring — the point appears as its cycle streams it."
                : picked.roundRead.status === "loading"
                  ? "Reading this searchpoint's round file…"
                  : "This searchpoint's round file does not hold it."
            }
            onOverlay={(next) =>
              setEdits(withOverlay(edits, picked.key, next, picked.cfg?.config ?? {}))
            }
            actions={
              selected &&
              picked.path && (
                <>
                <VerifyAction
                  candidate={selectedNodeOf(selected, picked.path.at(-1)?.cycleId ?? "")}
                  path={picked.path}
                />
                <SteerForkAction
                  candidate={selectedNodeOf(selected, picked.path.at(-1)?.cycleId ?? "")}
                  path={picked.path}
                  pipelineAt={picked.pipelineAt}
                />
                </>
              )
            }
          />
        </details>
        <ChannelRestore subjectKey={picked.key} />
        <CopyButton choices={copy.choices} title="Copy this searchpoint" />
      </div>
      <FailureLine failure={copy.cellsFailure} lead={COPY_WITHOUT_CELLS} />
    </>
  );
}
