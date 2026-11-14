import { useState } from "react";
import { Badge, CardFrame, DataTable, FailureLine, Term, type Column } from "@/components/ui";
import { MeasurementsPane } from "@/components/measurements/MeasurementsPane";
import { PairedLift } from "@/components/searchpoint/PairedLift";
import type { PairedReading, ServedArm } from "@/lib/api/types";
import { liftVersus } from "@/lib/derivations";
import { fmtLiftBand, fmtSigned, fmtUnit, PLACEHOLDER } from "@/lib/format";
import { failureOf, readyData } from "@/lib/hooks/useRead";
import { useRound } from "@/lib/hooks/useRound";
import type { CyclePath } from "@/lib/ids";

// A round FILE is the checkout of a served round: it renders that round, and the file only as raw text.
function ArmLift({ reading }: { reading: PairedReading | null }) {
  if (reading === null) return <>—</>;
  return (
    <PairedLift reading={reading}>
      {({ estimate, measurand }) => fmtLiftBand(measurand.unit, estimate)}
    </PairedLift>
  );
}

// The served order IS the rank: `RoundView.candidates` lists the round's arms as its scoreboard ranks them.
function scoreboardColumns(
  wonOnTheta: boolean,
  arms: readonly ServedArm[],
): Column<ServedArm>[] {
  // The lift column's sheet, as its readings name it: every arm's is the round's one.
  const cells = arms.find((r) => r.reading.vs_reference?.cell_set)?.reading.vs_reference?.cell_set;
  return [
    { id: "rank", label: "#", width: "3rem", cell: (r) => arms.indexOf(r) + 1 },
    {
      id: "candidate",
      label: "Candidate",
      width: "minmax(0, 3fr)",
      cell: ({ reading: s }) => (
        <span className="round-file-clip" title={s.changes_description}>
          {s.role === "parent" && <span className="round-file-dim">reference · </span>}
          {s.changes_description || s.arm.label}
        </span>
      ),
    },
    {
      id: "fitness",
      label: "Fitness",
      width: "6rem",
      align: "end",
      cell: ({ reading: s }) => (s.own ? fmtUnit(s.own.fitness_unit, s.own.fitness?.value) : PLACEHOLDER),
    },
    {
      id: "composite",
      label: "Composite",
      width: "6rem",
      align: "end",
      cell: ({ reading: s }) => fmtUnit("score", s.own?.composite?.value),
    },
    {
      id: "theta",
      label: (
        <Term
          content={`Difficulty-adjusted Rasch ability on the cycle's fixed δ ruler${wonOnTheta ? " — the metric the round winner is elected on, which is what explains a lower-fitness winner" : ""}. Empty outside the election fit, and for every row while the ruler is cold.`}
        >
          θ
        </Term>
      ),
      width: "5rem",
      align: "end",
      cell: ({ reading: s }) => fmtSigned(s.ability?.theta, 3),
    },
    {
      id: "lift",
      label: (
        <Term content="The candidate's blocked lift over the parent on the cells both measured, with its 95% interval. An interval spanning 0 means the round could not separate them.">
          Lift {liftVersus("parent", cells)}
        </Term>
      ),
      width: "minmax(0, 2fr)",
      cell: ({ reading: s }) => <ArmLift reading={s.vs_reference} />,
    },
    {
      id: "win",
      label: "Win",
      width: "3rem",
      cell: ({ reading: s }) => (s.election.selected ? <span className="pass">win</span> : ""),
    },
  ];
}

export function RoundFileView({
  path,
  round,
  raw,
}: {
  path: CyclePath;
  round: number;
  raw: string;
}) {
  const [showRaw, setShowRaw] = useState(false);
  const { read } = useRound(path, round);
  const doc = readyData(read);
  const failure = failureOf(read);
  const headline = doc?.headline ?? null;
  const election = doc?.election ?? null;
  const own = headline?.own;

  return (
    <div className="round-file-view">
      <div className="round-file-summary">
        <div className="round-file-summary-row">
          <Badge className="round-file-badge">round {round}</Badge>
          {own && (
            <>
              <span>fitness {fmtUnit(own.fitness_unit, own.fitness?.value)}</span>
              {Object.entries(headline.fitness_terms).map(([name, value]) => (
                <span key={name}>
                  {name} {fmtSigned(value, 3)}
                </span>
              ))}
              <span>composite {fmtUnit("score", own.composite?.value)}</span>
              <span>n {own.n}</span>
            </>
          )}
          {/* A round ending on its origin adopted it: it neither selects nor holds. */}
          {election &&
            headline?.role !== "origin" &&
            (election.selected !== null ? (
              <span className="pass">selected</span>
            ) : (
              <span className="round-file-dim">held</span>
            ))}
        </div>
        {election?.verdict_reason && (
          <div className="round-file-summary-row round-file-dim">{election.verdict_reason}</div>
        )}
        {election?.reelection && (
          <div className="round-file-summary-row round-file-dim">
            re-elected by {election.reelection.by.display}: {election.reelection.reason}
          </div>
        )}
        <FailureLine failure={failure} />
      </div>

      {doc && doc.candidates.length > 0 && (
        <CardFrame title={<span>Scoreboard</span>} actions={<Badge>{doc.candidates.length}</Badge>}>
          <DataTable
            columns={scoreboardColumns(election?.elects_on === "ability", doc.candidates)}
            rows={doc.candidates}
            getRowId={(r) => r.reading.arm.key}
            ariaLabel={`Round ${round} scoreboard`}
          />
        </CardFrame>
      )}

      {headline && (
        <MeasurementsPane
          preset={{ path, round, scope: "cycle", candidateId: headline.arm.key, groupBy: "none" }}
        />
      )}

      <details
        className="round-file-raw"
        open={showRaw}
        onToggle={(e) => setShowRaw((e.target as HTMLDetailsElement).open)}
      >
        <summary>Raw JSON ({raw.length.toLocaleString()} chars)</summary>
        <pre>{raw}</pre>
      </details>
    </div>
  );
}
