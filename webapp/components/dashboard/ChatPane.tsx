"use client";
import { useState } from "react";
import type { DashboardSnapshot } from "@/lib/poll";

interface Props {
  cycleId: string | null;
  sessionId: string | null;
  datasetTitle: string | null;
  dash: DashboardSnapshot | null;
}

function fmt(v: unknown): string {
  if (v == null || v === "") return "—";
  return String(v);
}

// Vanilla "New Job" pane, ported verbatim. Inert UI — chat input + most
// toggles are disabled. The wand-row toggle is the lone interactive element
// (purely visual, mirrors vanilla). Control plane lands in M12.
export function ChatPane({ cycleId, sessionId, datasetTitle, dash }: Props) {
  const [jobOpen, setJobOpen] = useState(false);
  const [wandOn, setWandOn] = useState(true);

  const best = typeof dash?.best === "number" ? dash.best : null;
  const accPct = best != null && Number.isFinite(best) ? `${(best * 100).toFixed(0)}% acc` : "— acc";
  const heroModel = (() => {
    const nodes = (dash?.current_round?.nodes as Record<string, { model?: string }> | undefined) ?? {};
    return nodes.l1_generate?.model ?? "idle";
  })();

  return (
    <div className="content chat-content" id="content-chat">
      <div className="chat-job-bar">
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
          <span>{datasetTitle || cycleId || "New Job"}</span>
          <svg className="chev" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="m3 4.5 3 3 3-3" />
          </svg>
        </button>
        {jobOpen && (
          <div className="chat-job-dropdown">
            <div className="row"><span className="lbl">Cycle</span><span className="val">{fmt(cycleId)}</span></div>
            <div className="row"><span className="lbl">Session</span><span className="val">{fmt(sessionId)}</span></div>
            <div className="row"><span className="lbl">Dataset</span><span className="val">{fmt(datasetTitle)}</span></div>
            <div className="row"><span className="lbl">Best</span><span className="val">{best != null ? `${best.toFixed(3)} (${accPct})` : "—"}</span></div>
            <div className="row"><span className="lbl">Updated</span><span className="val">{fmt(dash?.wallclock_serialized_at)}</span></div>
          </div>
        )}
      </div>

      <div className="wf-hero">
        <div className="wf-hero-status">
          <span className="dot" />
          <span>production · {accPct}</span>
        </div>
        <div className="wf-hero-flow">
          <div className="wf-hero-node">
            <div className="ico">
              <svg width="28" height="28" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="18 10 13 10 11.5 12.5 8.5 12.5 7 10 2 10" />
                <path d="M4.6 4.4 2 10v5a1.5 1.5 0 0 0 1.5 1.5h13a1.5 1.5 0 0 0 1.5-1.5v-5l-2.6-5.6a1.5 1.5 0 0 0-1.36-.9H5.96a1.5 1.5 0 0 0-1.36.9Z" />
              </svg>
            </div>
            <div className="text-col"><div className="lbl">Input</div><div className="val">Query</div></div>
          </div>
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
          <div className="wf-hero-node">
            <div className="ico">
              <svg width="28" height="28" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 2h6l4 4v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Z" />
                <path d="M11 2v5h4" />
                <path d="M6.5 12.5h6" />
                <path d="m10.5 10.5 2.5 2-2.5 2" />
              </svg>
            </div>
            <div className="text-col"><div className="lbl">Output</div><div className="val">Answer</div></div>
          </div>
        </div>
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
