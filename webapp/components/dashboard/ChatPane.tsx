"use client";
import { useEffect, useState } from "react";
import type { DashboardSnapshot } from "@/lib/poll";
import { TERMS } from "@/lib/terms";
import { FitnessPanel } from "@/components/whatif/FitnessPanel";
import { HardSamplesTable } from "@/components/dashboard/HardSamplesTable";

interface Props {
  cycleId: string | null;
  sessionId: string | null;
  datasetTitle: string | null;
  dash: DashboardSnapshot | null;
  cycleStartedAt: string | null;
  themeKey: string;
}

function fmt(v: unknown): string {
  if (v == null || v === "") return "—";
  return String(v);
}

// Format a duration in seconds as compact "Xh Ym" / "Xm" / "Xs".
function fmtDuration(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "—";
  if (sec < 90) return `${Math.round(sec)}s`;
  const m = Math.round(sec / 60);
  if (m < 90) return `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm === 0 ? `${h}h` : `${h}h ${rm}m`;
}

// Vanilla "New Job" pane, ported verbatim. Inert UI — chat input + most
// toggles are disabled. The wand-row toggle is the lone interactive element
// (purely visual, mirrors vanilla). Control plane lands in M12 — see
// docs/specs/m12-newjob-status-bar.md for the interactive write path.
export function ChatPane({ cycleId, sessionId, datasetTitle, dash, cycleStartedAt, themeKey }: Props) {
  const [jobOpen, setJobOpen] = useState(false);
  const [wandOn, setWandOn] = useState(true);
  const [samplesOpen, setSamplesOpen] = useState(false);
  const toggleSamples = () => setSamplesOpen((v) => !v);
  // Auto-open as soon as a cycle is bound — saves the operator one click per
  // page reload during dev. Manual close still sticks within the session.
  useEffect(() => {
    if (cycleId) setSamplesOpen(true);
  }, [cycleId]);

  const best = typeof dash?.best === "number" ? dash.best : null;
  const accPct = best != null && Number.isFinite(best) ? `${(best * 100).toFixed(0)}% acc` : "— acc";
  const bestPctOnly = best != null && Number.isFinite(best) ? `${(best * 100).toFixed(0)}%` : "—";
  const origin = typeof dash?.origin === "number" ? dash.origin : null;
  const originPct =
    origin != null && Number.isFinite(origin) ? `${(origin * 100).toFixed(0)}%` : null;

  // Spend block — written by LiveDashboardProjection from per-sample
  // step_tokens (backend bucket) + ledger TokenUsageRecord (loop bucket).
  // OpenRouter calls ship USD via the wire; other providers resolve
  // through shared/spend.py's rate table. When neither path produces a
  // figure (rate_known stays false), the chip falls back to a token-
  // count display ("1.2M tok") instead of pretending it's $0.
  type SpendBucket = {
    used_usd?: number;
    input_tokens?: number;
    output_tokens?: number;
    rate_known?: boolean;
    model?: string | null;
  };
  const spendBlock = (dash as Record<string, unknown> | null)?.spend as
    | {
        backend?: SpendBucket;
        loop?: SpendBucket;
        total_used_usd?: number;
        budget_usd?: number | null;
      }
    | undefined;
  const backendBucket = spendBlock?.backend ?? {};
  const loopBucket = spendBlock?.loop ?? {};
  const backendUsd = typeof backendBucket.used_usd === "number" ? backendBucket.used_usd : 0;
  const loopUsd = typeof loopBucket.used_usd === "number" ? loopBucket.used_usd : 0;
  const totalUsd =
    typeof spendBlock?.total_used_usd === "number"
      ? spendBlock.total_used_usd
      : backendUsd + loopUsd;
  const usedUsd = totalUsd > 0 ? totalUsd : null;
  const budgetUsd = typeof spendBlock?.budget_usd === "number" ? spendBlock.budget_usd : null;
  const rateKnown = !!(backendBucket.rate_known || loopBucket.rate_known);
  const totalTokens =
    (backendBucket.input_tokens ?? 0) +
    (backendBucket.output_tokens ?? 0) +
    (loopBucket.input_tokens ?? 0) +
    (loopBucket.output_tokens ?? 0);
  const fmtTokens = (n: number): string => {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M tok`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(0)}k tok`;
    return `${n} tok`;
  };
  const fmtUsd = (n: number): string => (n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`);
  const spendChip =
    rateKnown && usedUsd != null && usedUsd > 0
      ? fmtUsd(usedUsd)
      : totalTokens > 0
        ? fmtTokens(totalTokens)
        : "—";
  const spendTooltip =
    rateKnown && (backendUsd > 0 || loopUsd > 0)
      ? `Backend ${fmtUsd(backendUsd)} • Loop ${fmtUsd(loopUsd)}`
      : TERMS.newjob_bar_spend;
  const budgetChip = budgetUsd != null ? `$${budgetUsd.toFixed(2)}` : "—";
  const deltaPerSpend =
    best != null && origin != null && usedUsd != null && usedUsd > 0
      ? (best - origin) / usedUsd
      : null;
  const effChip =
    deltaPerSpend != null ? `${(deltaPerSpend * 100).toFixed(2)} pp/$` : "—";

  // ETA to budget — pure client-side derivation. Burn rate = used / cycle_age,
  // ETA = remaining_budget / burn. Renders "—" until spend is wired or when
  // budget is uncapped / already spent.
  const etaChip = (() => {
    if (usedUsd == null || budgetUsd == null || !cycleStartedAt) return "—";
    const startedMs = Date.parse(cycleStartedAt);
    if (!Number.isFinite(startedMs)) return "—";
    const ageSec = (Date.now() - startedMs) / 1000;
    if (ageSec <= 0 || usedUsd <= 0) return "—";
    if (usedUsd >= budgetUsd) return "spent";
    const burn = usedUsd / ageSec; // $/sec
    const remainingSec = (budgetUsd - usedUsd) / burn;
    return fmtDuration(remainingSec);
  })();

  const heroModel = (() => {
    const nodes = (dash?.current_round?.nodes as Record<string, { model?: string }> | undefined) ?? {};
    return nodes.l1_generate?.model ?? "idle";
  })();

  return (
    <div className="content chat-content" id="content-chat">
      <div className={`chat-job-bar${jobOpen ? " open" : ""}`}>
        <button
          type="button"
          className="chat-job-toggle"
          aria-expanded={jobOpen}
          onClick={() => setJobOpen((v) => !v)}
        >
          <svg className="grid" width="14" height="14" viewBox="0 0 14 14" fill="currentColor" aria-hidden="true">
            <rect x="1" y="1" width="5" height="5" rx="1" />
            <rect x="8" y="1" width="5" height="5" rx="1" opacity=".55" />
            <rect x="1" y="8" width="5" height="5" rx="1" opacity=".55" />
            <rect x="8" y="8" width="5" height="5" rx="1" opacity=".35" />
          </svg>
          <span className="ds">{datasetTitle || cycleId || "New Job"}</span>
          <span className="chip-row">
            <span className="chip" title={TERMS.newjob_bar_best}>
              <span className="chip-lbl">Best</span> <strong>{bestPctOnly}</strong>
              {originPct && <span className="chip-origin"> / {originPct}</span>}
            </span>
            <span className="chip" title={spendTooltip}>
              <span className="chip-lbl">Spend</span> <strong>{spendChip}</strong>
            </span>
            <span className="chip" title={TERMS.newjob_bar_budget}>
              <span className="chip-lbl">Budget</span> <strong>{budgetChip}</strong>
            </span>
            <span className="chip" title={TERMS.newjob_bar_eta}>
              <span className="chip-lbl">ETA</span> <strong>{etaChip}</strong>
            </span>
            <span className="chip" title={TERMS.newjob_bar_eff}>
              <span className="chip-lbl">Δ/$</span> <strong>{effChip}</strong>
            </span>
          </span>
          <svg className="chev" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="m3 4.5 3 3 3-3" />
          </svg>
        </button>
        {jobOpen && (
          <div className="chat-job-dropdown" role="region" aria-label="Job status and configuration">
            <div className="job-section">
              <div className="section-title">Identity</div>
              <div className="row"><span className="lbl">Cycle</span><span className="val">{fmt(cycleId)}</span></div>
              <div className="row"><span className="lbl">Session</span><span className="val">{fmt(sessionId)}</span></div>
              <div className="row"><span className="lbl">Dataset</span><span className="val">{fmt(datasetTitle)}</span></div>
              <div className="row"><span className="lbl">Updated</span><span className="val">{fmt(dash?.wallclock_serialized_at)}</span></div>
              <div className="section-title" style={{ marginTop: 12 }}>Spend</div>
              <div className="row"><span className="lbl">Backend</span><span className="val">{rateKnown ? fmtUsd(backendUsd) : `${(backendBucket.input_tokens ?? 0) + (backendBucket.output_tokens ?? 0)} tok`}</span></div>
              <div className="row"><span className="lbl">Loop</span><span className="val">{rateKnown ? fmtUsd(loopUsd) : `${(loopBucket.input_tokens ?? 0) + (loopBucket.output_tokens ?? 0)} tok`}</span></div>
              <div className="row"><span className="lbl">Total</span><span className="val">{usedUsd != null ? fmtUsd(usedUsd) : "—"}</span></div>
              <div className="row"><span className="lbl">Budget</span><span className="val">{budgetChip}</span></div>
            </div>
            <div className="job-whatif">
              <FitnessPanel dash={dash} themeKey={themeKey} />
            </div>
            <div className="job-footer" title={TERMS.newjob_bar_adjust}>
              Adjust spend / finishing criteria — wired in M12
            </div>
          </div>
        )}
      </div>

      <div className="wf-hero">
        <div className="wf-hero-status">
          <span className="dot" />
          <span>production · {accPct}</span>
        </div>
        <div className="wf-hero-flow">
          <button
            type="button"
            className="wf-hero-node wf-hero-node-toggle"
            aria-pressed={samplesOpen}
            aria-label={samplesOpen ? "Hide dataset preview" : "Show dataset preview"}
            onClick={toggleSamples}
          >
            <div className="ico">
              <svg width="28" height="28" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="18 10 13 10 11.5 12.5 8.5 12.5 7 10 2 10" />
                <path d="M4.6 4.4 2 10v5a1.5 1.5 0 0 0 1.5 1.5h13a1.5 1.5 0 0 0 1.5-1.5v-5l-2.6-5.6a1.5 1.5 0 0 0-1.36-.9H5.96a1.5 1.5 0 0 0-1.36.9Z" />
              </svg>
            </div>
            <div className="text-col"><div className="lbl">Input</div><div className="val">Query</div></div>
          </button>
          <div className="wf-hero-arrow" />
          <div className="wf-hero-node llm">
            <div className="head">
              <div className="ico">
                <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 2.5 6.5 18h11Z" fill="currentColor" fillOpacity="0.18" />
                  <path d="M5 18c2.4 1.6 4.7 2 7 2s4.6-.4 7-2" />
                  <path d="M5 18h14" />
                  <path d="m13.6 8.4.55 1.55 1.55.55-1.55.55-.55 1.55-.55-1.55-1.55-.55 1.55-.55Z" fill="currentColor" />
                  <circle cx="10.2" cy="13.2" r="0.7" fill="currentColor" />
                </svg>
              </div>
              <div className="lbl">LLM</div>
            </div>
            <div className="val">{heroModel}</div>
          </div>
          <div className="wf-hero-arrow" />
          <button
            type="button"
            className="wf-hero-node wf-hero-node-toggle"
            aria-pressed={samplesOpen}
            aria-label={samplesOpen ? "Hide dataset preview" : "Show dataset preview"}
            onClick={toggleSamples}
          >
            <div className="ico">
              <svg width="28" height="28" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 2h6l4 4v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Z" />
                <path d="M11 2v5h4" />
                <path d="M6.5 12.5h6" />
                <path d="m10.5 10.5 2.5 2-2.5 2" />
              </svg>
            </div>
            <div className="text-col"><div className="lbl">Output</div><div className="val">Answer</div></div>
          </button>
        </div>
        {samplesOpen && <HardSamplesTable cycleId={cycleId} dash={dash} />}
      </div>

      <div className="chat-grid">
        <div className="chat-panel">
          <div className="chat-panel-header">
            <div className="chat-panel-title">New Chat</div>
            <div className="chat-panel-status"><span className="dot" />connected</div>
          </div>
          <div className="chat-messages">
            <div className="chat-msg user">My pipeline above is stuck at 73%. Can&apos;t push past that.</div>
            <div className="chat-msg ai">Share the eval set you&apos;re scoring against?</div>
            <div className="chat-msg user user-file">
              <div className="file-chip">
                <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M14.5 7.5 8 14a3.5 3.5 0 0 1-4.95-4.95L9.5 2.6a2.4 2.4 0 0 1 3.4 3.4L6.4 12.5a1.3 1.3 0 0 1-1.83-1.83L11 4.2" />
                </svg>
                <span className="name">customer-tickets-eval.csv</span>
                <span className="meta">· 500 rows</span>
              </div>
            </div>
            <div className="chat-msg ai">Got your pipeline + the dataset. Flip on Auto-tune (BETA) — I&apos;ll find a better prompt for it. Want me to turn it on?</div>
            <div className="chat-msg ai">Which parameter do you want to explore?</div>
            <div className="chat-msg ai">
              Few things to tune this right:<br />
              • <strong>Which evaluators matter most?</strong> Easy picks: speed (time per query), # of websites checked, accuracy, cost per query — pick whatever you care about.<br />
              • <strong>Preferred LLM</strong>, or should I pick one?<br />
              • <strong>Pipeline type</strong> — LLM-driven (a model decides each step) or deterministic (fixed rules, no AI in the loop)?<br />
              • <strong>Time ceiling</strong> — any hard cap on how long one query is allowed to take?
            </div>
          </div>
          <div className="chat-input-row">
            <button className="chat-attach" type="button" title="Attach dataset" disabled aria-label="Attach dataset">
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14.5 7.5 8 14a3.5 3.5 0 0 1-4.95-4.95L9.5 2.6a2.4 2.4 0 0 1 3.4 3.4L6.4 12.5a1.3 1.3 0 0 1-1.83-1.83L11 4.2" />
              </svg>
            </button>
            <textarea className="chat-input" placeholder="Type a message…" rows={1} disabled aria-label="Chat input" />
            <button className="chat-send" type="button" disabled>Send</button>
          </div>
        </div>

        <div className="chat-settings">
          <div className="chat-settings-card">
            <div className="chat-settings-title">Settings</div>
            <div className="toggle-row">
              <div className="row-text">
                <span className="row-icon">
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                    <circle cx="8" cy="8" r="6" opacity=".3" />
                    <path d="M8 4v4l3 2" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
                  </svg>
                </span>
                <div className="row-body"><div className="name">Extended thinking</div></div>
              </div>
              <div className="toggle locked" />
            </div>
            <div className="toggle-row">
              <div className="row-text">
                <span className="row-icon">
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                    <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.2" fill="none" />
                    <path d="M2 8h12M8 2c2 1.8 2 10.2 0 12M8 2c-2 1.8-2 10.2 0 12" stroke="currentColor" strokeWidth="1.1" fill="none" />
                  </svg>
                </span>
                <div className="row-body"><div className="name">Web search</div></div>
              </div>
              <div className="toggle locked" />
            </div>
            <div className="toggle-row">
              <div className="row-text">
                <span className="row-icon">
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M5 4 1.5 8 5 12" />
                    <path d="M11 4l3.5 4L11 12" />
                    <path d="M9.5 3.5l-3 9" opacity=".6" />
                  </svg>
                </span>
                <div className="row-body"><div className="name">Code execution</div></div>
              </div>
              <div className="toggle locked" />
            </div>
            <div className="row-separator" />
            <div className="toggle-row wand-row">
              <div className="row-text">
                <span className="row-icon">
                  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M2.5 13.5 10 6" />
                    <path d="m12 1.5.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7Z" fill="currentColor" />
                    <path d="m5 2.4.4 1.1 1.1.4-1.1.4L5 5.4l-.4-1.1-1.1-.4 1.1-.4Z" fill="currentColor" opacity=".7" />
                  </svg>
                </span>
                <div className="row-body">
                  <div className="name">Optimize prompt while using<span className="beta-tag">Beta</span></div>
                  <div className="desc">Quietly evolves parameters across your dataset</div>
                </div>
              </div>
              <button
                type="button"
                className={`toggle${wandOn ? " on" : ""}`}
                role="switch"
                aria-checked={wandOn}
                aria-label="Optimize prompt while using"
                onClick={() => setWandOn((v) => !v)}
              />
              <span className="sparkle s1" /><span className="sparkle s2" /><span className="sparkle s3" /><span className="sparkle s4" />
              <span className="sparkle s5" /><span className="sparkle s6" /><span className="sparkle s7" /><span className="sparkle s8" />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
