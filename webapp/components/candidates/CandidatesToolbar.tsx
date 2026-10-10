"use client";
import { metricInkToken } from "./series";
import { AbilityHelp } from "./AbilityInfo";
import type { CandidatesModel } from "./useCandidatesModel";
import { setCandidatesState, toggleMetric } from "@/lib/candidates-store";
import {
  Badge,
  Chip,
  ChipGroup,
  CopyButton,
  HoverCard,
  IconMore,
  Menu,
  MenuCheck,
  MenuRadioGroup,
  MenuSep,
  Toolbar,
  ToolbarSep,
  ToolbarSpacer,
} from "@/components/ui";
import { ABORT_LENS_LABELS, DISPLAY_METRICS } from "@/lib/api/types.generated";
import { setScoringMask } from "@/lib/scoring-mask";
import { TERMS } from "@/lib/terms";

const LENS_OPTIONS: readonly { value?: string; label?: string; heading?: string }[] = [
  { value: "", label: "Realized" },
  { heading: "Scoring" },
  { value: "score:accuracy", label: "Accuracy" },
  { heading: "Abort off" },
  ...Object.entries(ABORT_LENS_LABELS).map(([variant, label]) => ({
    value: `abort:${variant}`,
    label,
  })),
];

export function CandidatesToolbar({ model: m }: { model: CandidatesModel }) {
  const { rung, hasOverlap, viewedPath } = m;
  const overlapNext = [
    hasOverlap
      ? "Read C0 and each new best since on the one set of cells all of them answered. The bars beside it stay on each candidate's own cells."
      : "Pick a set of cells and read every candidate that answered all of it on that one basis. There is no reading to show yet: the best-so-far line is still C0 alone, and a second member arrives with the first round whose result beats it.",
    "Choose which cells the overlap bars are read on — any round's set, or your own pick.",
    "Hide the overlap bars and drop the picked set.",
  ];

  return (
    <Toolbar className="cand-toolbar">
      {m.viewedCandidateId && viewedPath ? (
        <button
          type="button"
          className="cand-title cand-crumb"
          onClick={() => m.navigate(viewedPath)}
          title="Back to this course's candidates"
        >
          ‹ {m.viewedLabel} · runs
        </button>
      ) : (
        <span className="cand-title">Candidates</span>
      )}
      {m.maskActive && (
        <Badge
          tone="danger"
          title={`Showing the ${m.maskLabel} mask — divergence vs the realized record`}
        >
          {m.maskLabel}
        </Badge>
      )}
      <ToolbarSep />
      <ChipGroup label="Bars" joined>
        {DISPLAY_METRICS.map((d) => (
          <Chip
            key={d.id}
            icon
            on={m.metrics.has(d.id)}
            ink={`var(${metricInkToken(d.id, m.electedMetric)})`}
            ariaLabel={d.label}
            title={d.title}
            onClick={() => toggleMetric(d.id)}
          >
            {d.glyph}
          </Chip>
        ))}
        <Chip
          icon
          on={rung > 0}
          ink={rung === 2 ? "var(--color-new)" : "var(--color-overlap)"}
          disabled={m.overlapDisabled}
          ariaLabel={
            rung === 2
              ? "Choosing which cells the overlap bars are read on; press to turn them off"
              : rung === 1
                ? "Overlap shown — press to choose its cells"
                : hasOverlap
                  ? "Show the overlap reading — the adopted line on one shared set of cells"
                  : "Pick a set of cells to read the candidates on"
          }
          title={
            m.areCourses
              ? "These bars are runs, not scored cells — open a run to compare its candidates."
              : overlapNext[rung]
          }
          onClick={m.stepOverlap}
        >
          ∩
        </Chip>
      </ChipGroup>
      <ToolbarSpacer />
      <Menu
        renderTrigger={({ open, toggle }) => (
          <Chip
            icon
            on={open || m.lensActive || m.maskOpen || m.showCache}
            ariaLabel="More candidate options"
            title="Lens, scoring mask, cache overlay, and the θ explainer"
            onClick={toggle}
          >
            <IconMore />
          </Chip>
        )}
      >
        {({ close }) => (
          <>
            <MenuRadioGroup
              label={m.scoringMaskActive ? "Lens — driven by the scoring mask" : "Lens"}
              value={m.scoringMaskActive ? "" : m.lens}
              options={LENS_OPTIONS}
              onChange={(v) => {
                if (m.scoringMaskActive) return;
                m.setLens(v);
                close();
              }}
            />
            <MenuSep />
            <HoverCard content="Pick per-cell terms and reweight them to re-read every score under a criterion you choose.">
              <MenuCheck on={m.maskOpen} onClick={() => setScoringMask({ open: !m.maskOpen })}>
                Scoring mask
              </MenuCheck>
            </HoverCard>
            <HoverCard content={TERMS.cache_replayed}>
              <MenuCheck
                on={m.showCache}
                onClick={() => setCandidatesState({ showCache: !m.showCache })}
              >
                {/* "Replayed", never "cache": `cache` names the provider's prefix discount elsewhere. */}
                Replayed{m.cacheHitCount > 0 ? ` · ${m.cacheHitCount} of ${m.views.length}` : ""}
              </MenuCheck>
            </HoverCard>
            <MenuSep />
            <MenuCheck
              on={m.compareOn}
              disabled={m.compareDisabled}
              onClick={() => {
                if (m.compareDisabled) return;
                m.toggleCompare();
                close();
              }}
              title={
                m.hasCompareKey
                  ? "Read this searchpoint beside other campaigns, branches and searchpoints on the Compare tab."
                  : "Pick a candidate first — a bar, a dendrogram node or a forest stub."
              }
            >
              Compare this searchpoint
            </MenuCheck>
            {m.wonOnTheta && (
              <>
                <MenuSep />
                <MenuCheck
                  on={m.showTheta}
                  onClick={m.toggleTheta}
                  title="Why a lower-accuracy candidate can win"
                >
                  How candidates are ranked
                </MenuCheck>
              </>
            )}
            {m.wonOnTheta && m.showTheta && (
              <AbilityHelp
                model={m.ability?.calibration_model ?? null}
                caveat={m.ability?.caveat ?? null}
              />
            )}
          </>
        )}
      </Menu>
      <CopyButton data={m.views} title="Copy all candidates as JSON" />
    </Toolbar>
  );
}
