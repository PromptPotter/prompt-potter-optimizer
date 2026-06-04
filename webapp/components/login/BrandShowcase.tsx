import { BRAND } from "@/lib/brand";

// Bare host for the CTA ("promptpotter.com"). Falls back to the raw string if
// it isn't a parseable URL — the value is build-time config.
function siteHost(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
}

// Checkbox glyph — filled cobalt tick (done) or outlined square (pending).
// Inline SVG keeps the showcase dependency-free (no icon webfont).
function Check({ done }: { done: boolean }) {
  return done ? (
    <svg className="ls-check ls-check-done" viewBox="0 0 16 16" aria-hidden="true">
      <rect x="0.5" y="0.5" width="15" height="15" rx="3.5" />
      <path d="M4 8.2 6.8 11 12 5" fill="none" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ) : (
    <svg className="ls-check ls-check-todo" viewBox="0 0 16 16" aria-hidden="true">
      <rect x="0.75" y="0.75" width="14.5" height="14.5" rx="3.5" fill="none" strokeWidth="1.3" />
    </svg>
  );
}

const BEHAVIORS: { label: string; done: boolean }[] = [
  { label: "Normalize units before lookup", done: true },
  { label: "Return the exact catalog ID", done: true },
  { label: "Resolve ambiguous abbreviations", done: false },
  { label: "Cite the matched source row", done: false },
];

// Gantt bars — [label, left%, width%, tone]. Offset timeline mapped to the
// L1/L2/L3 escalation rounds.
const STAGES: { label: string; left: number; width: number; tone?: "success" }[] = [
  { label: "L1 · generate", left: 1, width: 32 },
  { label: "L2 · refine", left: 22, width: 34 },
  { label: "L3 · escalate", left: 48, width: 30 },
  { label: "converged", left: 71, width: 27, tone: "success" },
];

// The login right-pane brand moment: one large, colorful, self-contained
// advertising composition — NOT operator chrome. It carries its own brand
// palette (theme-independent on purpose) and reads its copy/URL from
// `BRAND.marketing`, so a whitelabel host either re-skins by replacing this
// one file or drops it wholesale by clearing NEXT_PUBLIC_MARKETING_URL.
export function BrandShowcase() {
  const { url, title, tagline } = BRAND.marketing;

  return (
    <aside className="login-showcase" aria-label={`About ${title}`}>
      <div className="ls-aurora" aria-hidden="true" />

      <div className="ls-content">
        <div className="ls-eyebrow">
          <svg className="ls-mark" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M12 1.6 21.5 7v10L12 22.4 2.5 17V7z"
              stroke="currentColor"
              strokeWidth={1.4}
              strokeLinejoin="round"
            />
            <path d="M12 6.8 8 14.8h8z" fill="currentColor" />
            <path d="M8 16.9h8" stroke="currentColor" strokeWidth={1.4} strokeLinecap="round" />
            <path
              d="M3 12h3.4M17.6 12H21"
              stroke="currentColor"
              strokeWidth={1.4}
              strokeLinecap="round"
            />
          </svg>
          <span>{title}</span>
        </div>

        <h2 className="ls-headline">
          Fix a broken LLM pipeline <em>in half a day.</em> Then it just works.
        </h2>
        <p className="ls-lead">{tagline}</p>

        {/* One composed scene — overlapping panels, not a row of cards. */}
        <div className="ls-art" role="img" aria-label="Live optimization run preview">
          <div className="ls-panel ls-panel-chart">
            <div className="ls-panel-head">
              <span className="ls-score">0.94</span>
              <span className="ls-delta">+0.31 vs origin</span>
              <span className="ls-panel-label">composite fitness · round 4 / 5</span>
            </div>
            <svg className="ls-curve" viewBox="0 0 400 140" preserveAspectRatio="none" aria-hidden="true">
              <line x1="0" y1="38" x2="400" y2="38" className="ls-grid" />
              <line x1="0" y1="78" x2="400" y2="78" className="ls-grid" />
              <path
                d="M 0 100 L 80 88 L 160 64 L 240 38 L 320 24 L 400 18 L 400 140 L 0 140 Z"
                className="ls-area"
              />
              <path d="M 0 100 L 80 88 L 160 64 L 240 38 L 320 24 L 400 18" className="ls-line" />
              <circle cx="240" cy="38" r="3" className="ls-dot" />
              <circle cx="320" cy="24" r="3" className="ls-dot" />
              <circle cx="400" cy="18" r="4" className="ls-dot ls-dot-head" />
            </svg>
            <div className="ls-meta">
              <span>
                <b>16</b> candidates
              </span>
              <span>
                <b>1,248</b> scored
              </span>
              <span>
                <b>$2.41</b> spend
              </span>
            </div>
          </div>

          {/* Behavior-checks panel — analogy to the reference "Final QA" list. */}
          <div className="ls-panel ls-panel-checks" aria-hidden="true">
            <div className="ls-checks-head">
              <span className="ls-checks-title">Behavior checks</span>
              <span className="ls-checks-count">18 / 20</span>
            </div>
            <ul className="ls-checks-list">
              {BEHAVIORS.map((b) => (
                <li key={b.label} className={b.done ? "done" : undefined}>
                  <Check done={b.done} />
                  <span>{b.label}</span>
                </li>
              ))}
            </ul>
          </div>

          {/* Run-timeline gantt — analogy to the reference "Launch tracker". */}
          <div className="ls-panel ls-panel-timeline" aria-hidden="true">
            <div className="ls-tl-head">
              <span className="ls-tl-title">Run timeline</span>
              <span className="ls-tl-chip">termnorm</span>
            </div>
            <div className="ls-tl-ticks">
              {["R0", "R1", "R2", "R3", "R4", "R5"].map((r) => (
                <span key={r}>{r}</span>
              ))}
            </div>
            <div className="ls-tl-gantt">
              {STAGES.map((s) => (
                <div
                  key={s.label}
                  className={s.tone === "success" ? "ls-tl-bar success" : "ls-tl-bar"}
                  style={{ marginLeft: `${s.left}%`, width: `${s.width}%` }}
                >
                  {s.label}
                </div>
              ))}
            </div>
          </div>
        </div>

        {url ? (
          <a className="ls-cta" href={url} target="_blank" rel="noopener noreferrer">
            Visit {siteHost(url)}
            <span aria-hidden="true"> →</span>
          </a>
        ) : null}
      </div>
    </aside>
  );
}
