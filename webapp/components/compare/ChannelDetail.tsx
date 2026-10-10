"use client";

import { SteerForkAction } from "@/components/shell/searchpoint/SteerForkAction";
import { VerifyAction } from "@/components/shell/searchpoint/VerifyAction";
import type { CladogramChannel } from "@/components/candidates/Forest";
import { SearchpointDrillIn } from "@/components/shell/searchpoint/SearchpointDrillIn";
import { MeasurementsPane } from "@/components/shell/measurements/MeasurementsPane";
import { searchpointCopyChoices, selectedNodeOf, type CompareItem } from "@/lib/derivations";
import { fmtUsd, shortId } from "@/lib/format";
import { CopyButton, SegmentedControl } from "@/components/ui";
import { ChannelMap } from "./ChannelMap";
import { CellMeans } from "./channel-parts";
import { ChannelRestore, useScenarioEdits, withOverlay } from "./config-edit";
import type { ChannelModel } from "./useChannelModel";

export function ChannelDetail({
  model,
  item: { reading, headline },
  rulerNote,
  points,
  own,
  axis,
  inColumn,
}: {
  model: ChannelModel;
  item: CompareItem;
  rulerNote: boolean;
  points: readonly CladogramChannel[];
  own: CladogramChannel | null;
  axis: string;
  inColumn: boolean;
}) {
  const { edits, setEdits } = useScenarioEdits();
  const { folds, head, selected, setSelected, picked, level, spentTo } = model;
  if (!folds.detail) return null;
  const map = <ChannelMap model={model} reading={reading} points={points} own={own} />;
  if (reading === null) return map;
  if (!level) return null;
  return (
    <>
      {!inColumn && head.options.length > 1 && (
        <SegmentedControl
          options={head.options}
          value={head.value}
          onChange={(v) => setSelected(head.nodeFor(v))}
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
              <dd>{headline.arm ? headline.arm.arm_key : "none"}</dd>
            </div>
            <div>
              <dt>treatment</dt>
              <dd title={headline.treatment_digest ?? undefined}>
                {headline.treatment_digest?.slice(0, 8) ?? "—"}
              </dd>
            </div>
            <div>
              <dt>bench reads</dt>
              <dd>{headline.bench.status.reads_before ?? "—"}</dd>
            </div>
          </>
        )}
        <div>
          <dt>{spentTo !== null ? `spent to ${selected?.label}` : "branch spend"}</dt>
          <dd>
            {spentTo !== null
              ? fmtUsd(spentTo)
              : reading.cycle_spend_usd != null
                ? fmtUsd(reading.cycle_spend_usd)
                : "—"}
          </dd>
        </div>
        <div>
          <dt>dataset</dt>
          <dd title={reading.dataset_name}>{reading.dataset_name || "—"}</dd>
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
          <dd title={reading.authorship}>{reading.authorship || "—"}</dd>
        </div>
        <div>
          <dt>replayed cells</dt>
          <dd>{reading.cached_samples ?? "—"}</dd>
        </div>
      </dl>
      {rulerNote && headline && <p className="note-warn">{reading.comparable_note}</p>}

      <p className="cmp-channel-lineage">
        <button
          type="button"
          className="cmp-link"
          aria-expanded={folds.map}
          onClick={() => model.toggleFold("map")}
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
              model.toggleFold("setup");
            }}
          >
            <span>
              {selected
                ? `${selected.label} · round ${selected.reading.arm.round}`
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
            stats={picked.isOwn && <CellMeans means={reading.cell_means} />}
            measurements={
              picked.path &&
              picked.id && (
                <MeasurementsPane
                  preset={{
                    path: picked.path,
                    datasetName: reading.dataset_name,
                    candidateId: picked.id,
                    scope: "cycle",
                    groupBy: "none",
                  }}
                />
              )
            }
            arms={picked.arms}
            schema={picked.schema}
            overlay={picked.overlay}
            pending={
              picked.docLoading
                ? "Reading this searchpoint's round file…"
                : "No round file on disk for this point — a round still scoring has not written one yet, and this tab streams no cycle to read it from."
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
                  schema={picked.schema}
                />
                </>
              )
            }
          />
        </details>
        <ChannelRestore subjectKey={picked.key} />
        <CopyButton
          choices={searchpointCopyChoices({
            cfg: picked.cfg,
            reading: picked.reading,
            samples: picked.samples,
            arms: picked.arms,
          })}
          title="Copy this searchpoint"
        />
      </div>
    </>
  );
}
