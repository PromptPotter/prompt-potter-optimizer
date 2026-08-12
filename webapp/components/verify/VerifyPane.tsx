"use client";
// Workspace-scope diagnostic-run records — one row per `verify` CLI invocation.
// Categorically NOT a cycle, fork, or sweep: pure on-demand re-evaluation of
// an existing candidate against more samples. Reads GET /api/v1/workspace/
// diagnostic-runs and renders a sortable table with a per-row trend bar
// (grey = source-campaign composite, red overlay = workspace composite).

import { fetchDiagnosticRuns, type DiagnosticRunRecord } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { ageText, fmtFitness, fmtPct0 } from "@/lib/format";
import { useFetch } from "@/lib/hooks/useFetch";
import { ErrorNote, Loading, SignInPrompt } from "@/components/ui";

export function VerifyPane() {
  const { status } = useAuth();
  // Gate on a confirmed session — `null` fetcher means useFetch fires nothing,
  // so anon never 401s the protected read (frontend-surface-contract.md § I5).
  const { data, error } = useFetch(
    status === "authed" ? (s) => fetchDiagnosticRuns(undefined, s) : null,
    [status],
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

  if (error) {
    return (
      <div className="verify-pane">
        <ErrorNote>Couldn’t load diagnostic runs — retry shortly.</ErrorNote>
      </div>
    );
  }
  if (data === null) {
    return (
      <div className="verify-pane">
        <Loading>Loading diagnostic runs…</Loading>
      </div>
    );
  }

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
              <th title="Samples requested at the CLI; how many were newly measured (the rest were already in the cross-cycle archive).">Samples</th>
              <th title="Total samples this candidate has measurements for across the dataset's archive.">Workspace n</th>
              <th title="Source campaign's accuracy for this candidate, as persisted on the round file.">Campaign acc</th>
              <th title="Workspace accuracy = mean hit rate over the workspace measurement set.">Workspace acc</th>
              <th title="Source campaign's composite for this candidate, as persisted on the round file.">Campaign cf</th>
              <th title="Composite recomputed under the campaign's scorer over every workspace measurement for this candidate's config.">Workspace cf</th>
              <th title="Grey = source-campaign accuracy. Red overlay = workspace accuracy. A red bar shorter than the grey one means the verdict didn't hold.">Trend</th>
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
        <TrendBar source={run.source_campaign_accuracy} workspace={run.workspace_accuracy} />
      </td>
      <td className="verify-when">{ageText(run.ts)}</td>
    </tr>
  );
}

// Two-segment overlay: grey strip = source-campaign accuracy, red overlay
// = workspace accuracy. Accuracy is the primary metric matched against the
// dashboard's per-candidate fitness bars (blue = accuracy). Both clamped to
// [0,1].
function TrendBar({ source, workspace }: { source: number; workspace: number }) {
  const clamp = (v: number) => Math.max(0, Math.min(1, v));
  const s = clamp(source);
  const w = clamp(workspace);
  const held = workspace + 1e-9 >= source;
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
