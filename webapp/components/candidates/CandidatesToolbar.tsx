import { metricInkToken } from "./series";
import { AbilityHelp } from "./AbilityInfo";
import type { CandidatesModel } from "./useCandidatesModel";
import { setCandidatesState } from "@/lib/state/candidates";
import {
  Badge,
  Chip,
  ChipGroup,
  CopyButton,
  Term,
  HoverCard,
  IconDownload,
  IconMore,
  Menu,
  MenuCheck,
  MenuItem,
  MenuRadioGroup,
  MenuSep,
  Toolbar,
  ToolbarSep,
  ToolbarSpacer,
} from "@/components/ui";
import { exportText, exportUrl } from "@/lib/api/reads";
import {
  ABORT_LENS_LABELS,
  CANDIDATE_COUNT_WORDS,
  DISPLAY_METRIC_INFO,
  EXPORT_TIER_INFO,
  PREFIX_STATE_TITLES,
  SCORE_LENS_LABELS,
} from "@/lib/api/types";
import { wordRows } from "@/lib/api/words";
import { setScoringMask } from "@/lib/scoring-mask";

const lensOptions = (labels: Record<string, string>) =>
  wordRows(labels).map(([value, label]) => ({ value, label }));

const LENS_OPTIONS: readonly { value?: string; label?: string; heading?: string }[] = [
  { value: "", label: "Realized" },
  { heading: "Scoring" },
  ...lensOptions(SCORE_LENS_LABELS),
  { heading: "Abort off" },
  ...lensOptions(ABORT_LENS_LABELS),
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
      {m.drilled && viewedPath ? (
        <button
          type="button"
          className="cand-title cand-crumb"
          onClick={() => m.pickCandidate(null)}
          title="Back to this course's candidates"
        >
          ‹ {m.viewedLabel} · runs
        </button>
      ) : (
        <span className="cand-title">Candidates</span>
      )}
      {m.readUnder && (
        <Term content={m.readUnder.detail ?? m.readUnder.label}>
          <Badge tone={m.readUnder.tone}>{m.readUnder.label}</Badge>
        </Term>
      )}
      <ToolbarSep />
      <ChipGroup label="Bars" joined>
        {wordRows(DISPLAY_METRIC_INFO).map(([id, d]) => (
          <Chip
            key={id}
            icon
            on={m.metrics.has(id)}
            ink={`var(${metricInkToken(id, m.electedMetric)})`}
            ariaLabel={d.label}
            title={d.title}
            onClick={() => m.toggleMetric(id)}
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
              ? "Bars are runs: open one to compare its candidates"
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
            expanded={open}
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
            <HoverCard
              content={
                m.cacheHitCount > 0
                  ? `${PREFIX_STATE_TITLES.replayed} ${CANDIDATE_COUNT_WORDS.replayed}`
                  : PREFIX_STATE_TITLES.replayed
              }
            >
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
                  ? "Read this searchpoint beside others on the Compare tab"
                  : "Pick a candidate first: a bar, a node or a forest stub"
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
                model={m.ability?.reading.calibration_model ?? null}
                caveat={m.ability?.caveat ?? null}
              />
            )}
          </>
        )}
      </Menu>
      {viewedPath && (
        <>
          <CopyButton
            title="Copy this run's candidates"
            choices={wordRows(EXPORT_TIER_INFO).filter(([, t]) => t.pasted).map(([id, t]) => ({
              key: id,
              label: t.label,
              data: () => exportText(viewedPath, id),
            }))}
          />
          <Menu
            align="right"
            renderTrigger={({ open, toggle }) => (
              <Chip
                icon
                on={open}
                expanded={open}
                ariaLabel="Download this run"
                title="Download this run"
                onClick={toggle}
              >
                <IconDownload />
              </Chip>
            )}
          >
            {({ close }) =>
              wordRows(EXPORT_TIER_INFO).filter(([, t]) => !t.pasted).map(([id, t]) => (
                <MenuItem
                  key={id}
                  onClick={() => {
                    close();
                    window.location.assign(exportUrl(viewedPath, id));
                  }}
                >
                  {t.label}
                </MenuItem>
              ))
            }
          </Menu>
        </>
      )}
    </Toolbar>
  );
}
