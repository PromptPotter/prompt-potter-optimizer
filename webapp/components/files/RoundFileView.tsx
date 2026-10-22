"use client";
import { useState } from "react";
import { Badge, CardFrame, DataTable, ErrorNote, Term, type Column } from "@/components/ui";
import { MeasurementsPane } from "@/components/shell/measurements/MeasurementsPane";
import { PairedLift } from "@/components/shell/PairedLift";
import type { PairedReading, ScoreboardRow } from "@/lib/api/types";
import { fmtNum, fmtPct1, fmtSigned } from "@/lib/format";
import { useRound } from "@/lib/hooks/useRound";
import type { CyclePath } from "@/lib/ids";

// A round FILE is the checkout of a served round: it renders that round, and the file only as raw text.
function ArmLift({ reading }: { reading: PairedReading | null }) {
  if (reading === null) return <>—</>;
  return (
    <PairedLift reading={reading}>
      {({ estimate }) =>
        `${fmtSigned(estimate.value, 3)} [${fmtSigned(estimate.ci_lo, 3)}, ${fmtSigned(estimate.ci_hi, 3)}]`
      }
    </PairedLift>
  );
}

function scoreboardColumns(wonOnTheta: boolean): Column<ScoreboardRow>[] {
  return [
    { id: "rank", label: "#", width: "3rem", cell: (r) => r.rank },
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
      id: "accuracy",
      label: "Accuracy",
      width: "6rem",
      align: "end",
      cell: ({ reading: s }) => fmtPct1(s.own?.accuracy?.value),
    },
    {
      id: "composite",
      label: "Composite",
      width: "6rem",
      align: "end",
      cell: ({ reading: s }) => fmtNum(s.own?.composite?.value),
    },
    {
      id: "theta",
      label: (
        <Term
          content={`Difficulty-adjusted Rasch ability on the cycle's fixed δ ruler${wonOnTheta ? " — the metric the round winner is elected on, which is what explains a lower-accuracy winner" : ""}. Empty outside the election fit, and for every row while the ruler is cold.`}
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
          Lift vs parent
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
  const { doc, failure } = useRound(path, round);
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
              <span>accuracy {fmtPct1(own.accuracy?.value)}</span>
              <span>composite {fmtNum(own.composite?.value)}</span>
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
        {failure && <ErrorNote>{failure.message}</ErrorNote>}
      </div>

      {doc && doc.scoreboard.length > 0 && (
        <CardFrame title={<span>Scoreboard</span>} actions={<Badge>{doc.scoreboard.length}</Badge>}>
          <DataTable
            columns={scoreboardColumns(election?.elects_on === "ability")}
            rows={doc.scoreboard}
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
