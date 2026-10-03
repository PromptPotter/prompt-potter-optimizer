"use client";
// One row per `verify` CLI invocation — a re-measure of an existing candidate on more samples,
// never a cycle or a fork.

import { fetchDiagnosticRuns, type DiagnosticRunRecord } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { ageText, fmtFitness, fmtPct0 } from "@/lib/format";
import { useRead } from "@/lib/hooks/useRead";
import { ErrorNote, Loading, SignInPrompt, Term } from "@/components/ui";

export function VerifyPane() {
  const { status } = useAuth();
  const read = useRead(
    { key: "diagnostic-runs", fetch: (s) => fetchDiagnosticRuns(undefined, s) },
    { surface: "diagnostic-runs", auth: true },
  );

  if (status !== "authed") {
    return (
      <div className="verify-pane">
        {status === "loading" ? (
          <Loading />
        ) : (
          <SignInPrompt message="Sign in to view workspace verification runs." />
        )}
      </div>
    );
  }

  if (read.status === "failed") {
    return (
      <div className="verify-pane">
        <ErrorNote>Couldn’t load diagnostic runs — retry shortly.</ErrorNote>
      </div>
    );
  }
  if (read.status !== "ready") {
    return (
      <div className="verify-pane">
        <Loading>Loading diagnostic runs…</Loading>
      </div>
    );
  }
  const data = read.data;

  if (data.n === 0) {
    return (
      <div className="verify-pane">
        <header className="verify-header">
          <h2>Workspace verification</h2>
          <p className="verify-subtitle">
            No diagnostic runs yet. Re-score a candidate from any campaign on
            more samples with:
          </p>
          <pre className="verify-cli">python -m promptpotter verify &lt;campaign&gt; &lt;label&gt; --samples 20</pre>
          <p className="verify-subtitle">
            e.g. <code>python -m promptpotter verify ca6d4d C4.1 --samples 20</code>.
            Each invocation reuses the cross-cycle archive: only samples this
            candidate has never been measured on cost fresh LLM calls.
          </p>
        </header>
      </div>
    );
  }

  return (
    <div className="verify-pane">
      <header className="verify-header">
        <h2>Workspace verification</h2>
        <p className="verify-subtitle">
          One row per <code>verify</code> invocation. Compares the source
          campaign&apos;s per-candidate accuracy + composite against the same
          metrics recomputed over every measurement the cross-cycle archive
          holds for that candidate&apos;s config (dataset-scoped).
        </p>
      </header>
      <div className="verify-table-wrap">
        <table className="verify-table">
          <thead>
            <tr>
              <th>Source</th>
              <th><Term content="Samples requested at the CLI; how many were newly measured (the rest were already in the cross-cycle archive).">Samples</Term></th>
              <th><Term content="Total samples this candidate has measurements for across the dataset's archive.">Workspace n</Term></th>
              <th><Term content="Source campaign's accuracy for this candidate, as persisted on the round file.">Campaign acc</Term></th>
              <th><Term content="Workspace accuracy = mean hit rate over the workspace measurement set.">Workspace acc</Term></th>
              <th><Term content="Source campaign's composite for this candidate, as persisted on the round file.">Campaign cf</Term></th>
              <th><Term content="Composite recomputed under the campaign's scorer over every workspace measurement for this candidate's config.">Workspace cf</Term></th>
              <th><Term content="Grey = source-campaign accuracy. Red overlay = workspace accuracy. A red bar shorter than the grey one means the verdict didn't hold.">Trend</Term></th>
              <th>When</th>
            </tr>
          </thead>
          <tbody>
            {data.runs.map((r) => (
              <VerifyRow key={`${r.ts}-${r.config_hash}`} run={r} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function VerifyRow({ run }: { run: DiagnosticRunRecord }) {
  const cacheReplays = Math.max(0, run.workspace_n - run.samples_added - run.source_campaign_n);
  return (
    <tr>
      <td className="verify-source">
        <code>{run.source_campaign}</code>
        <span className="verify-sep"> / </span>
        <strong>{run.source_label}</strong>
      </td>
      <td className="verify-num">
        <span>{run.samples_requested}</span>
        <span className="verify-add"> (+{run.samples_added} new{cacheReplays ? `, ${cacheReplays} cached` : ""})</span>
      </td>
      <td className="verify-num"><strong>{run.workspace_n}</strong></td>
      <td className="verify-num">{fmtPct0(run.source_campaign_accuracy)}</td>
      <td className="verify-num">{fmtPct0(run.workspace_accuracy)}</td>
      <td className="verify-num">{fmtFitness(run.source_campaign_composite)}</td>
      <td className="verify-num">{fmtFitness(run.workspace_composite)}</td>
      <td className="verify-bar-cell">
        {run.source_campaign_accuracy === null || run.held === null ? (
          "—"
        ) : (
          <TrendBar
            source={run.source_campaign_accuracy}
            workspace={run.workspace_accuracy}
            held={run.held}
          />
        )}
      </td>
      <td className="verify-when">{ageText(run.ts)}</td>
    </tr>
  );
}

// `held` is SERVED: the producer owns when two measured rates count as equal, so no surface picks
// its own epsilon.
function TrendBar({
  source,
  workspace,
  held,
}: {
  source: number;
  workspace: number;
  held: boolean;
}) {
  const clamp = (v: number) => Math.max(0, Math.min(1, v));
  const s = clamp(source);
  const w = clamp(workspace);
  return (
    <div className="verify-bar" role="img" aria-label={`workspace ${held ? "≥" : "<"} campaign accuracy`}>
      <div className="verify-bar-source" style={{ width: `${s * 100}%` }} />
      <div
        className={`verify-bar-workspace${held ? " held" : " dropped"}`}
        style={{ width: `${w * 100}%` }}
      />
    </div>
  );
}
