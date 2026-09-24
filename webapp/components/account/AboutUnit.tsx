"use client";
// "About this unit" — a reader of the identity the app already publishes (brand, the <head>
// JSON-LD, the server's version), never a parallel manifest.

import { useState } from "react";
import { PotterMark } from "@/components/brand/PotterMark";
import { CopyButton } from "@/components/ui";
import { BRAND, softwareApplicationLd } from "@/lib/brand";
import { fetchHealth } from "@/lib/api";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { formatDiagnostics, useIncidents } from "@/lib/diagnostics";
import { cx } from "@/lib/cx";

export function AboutUnit() {
  const [showHow, setShowHow] = useState(false);
  const [showRaw, setShowRaw] = useState(false);

  const health = readyData(useRead({ key: "health", fetch: fetchHealth }, { surface: "health" }));
  const version = health?.version ?? null;
  const incidents = useIncidents();

  const verified = BRAND.verification === "verified";
  const raw = JSON.stringify(softwareApplicationLd(), null, 2);

  return (
    <>
      <div className="about-unit-head">
        <span className="about-unit-mark" aria-hidden="true">
          <Mark />
        </span>
        <div className="about-unit-id">
          <span className="about-unit-name">{BRAND.name}</span>
          <span className="about-unit-tagline">{BRAND.description}</span>
        </div>
        <ProvenancePill verified={verified} />
      </div>

      <div className="account-row">
        <span className="account-label">Powered by</span>
        <div className="account-row-main">
          <OrgLine org={BRAND.provider} />
          <p className="account-muted">
            The technology provider that operates this unit. Fixed — it travels with
            the unit wherever it&rsquo;s embedded.
          </p>
        </div>
      </div>

      <div className="account-row">
        <span className="account-label">Published by</span>
        <div className="account-row-main">
          <OrgLine org={BRAND.publisher} />
          <p className="account-muted">
            The brand distributing this unit to you. Set by the host.
          </p>
        </div>
      </div>

      <div className="account-row">
        <span className="account-label">Version</span>
        <div className="account-row-main">
          <span className="about-unit-mono">{version ?? "—"}</span>
          <p className="account-muted">Reported live by the server.</p>
        </div>
      </div>

      <div className="account-row">
        <span className="account-label">Provenance</span>
        <div className="account-row-main">
          <ProvenancePill verified={verified} />
          <p className="account-muted">
            {verified
              ? "This unit carries a signed, origin-bound credential, verified at load."
              : "This unit declares its identity through the standard surfaces below. Cryptographic verification (a signed, origin-bound credential) is the next step on the roadmap — not in place yet."}
          </p>
          <button
            type="button"
            className="about-unit-disclosure-trigger"
            aria-expanded={showHow}
            onClick={() => setShowHow((v) => !v)}
          >
            <span className={cx("about-unit-caret", showHow && "open")} aria-hidden="true">
              ›
            </span>
            How this works
          </button>
          <div className={cx("about-unit-disclosure", showHow && "open")}>
            <div className="about-unit-disclosure-inner">
              <p>
                This unit declares who makes it through the same standards any web
                app uses: a{" "}
                <span className="about-unit-mono">schema.org/SoftwareApplication</span>{" "}
                record embedded in the page (readable by crawlers and agents) and a
                Web App Manifest the browser reads. Both name two distinct parties —
                the <strong>provider</strong> that powers the unit and the{" "}
                <strong>publisher</strong> that distributes it.
              </p>
              <p>
                Today that declaration is <strong>self-declared</strong>: the unit
                states its identity and these surfaces render it faithfully. The
                roadmap upgrades this to a verifiable <strong>certificate</strong> — a
                content hash plus a signed, origin-bound token — at which point this
                panel can show a <em>verified</em> state backed by a signature rather
                than a declaration.
              </p>
            </div>
          </div>
        </div>
      </div>

      <div className="account-row">
        <span className="account-label">Resources</span>
        <div className="account-row-main">
          <ul className="about-unit-links">
            <li>
              <ResourceLink href={BRAND.legal.privacy} label="Data handling & legal" />
            </li>
            <li>
              <ResourceLink href={BRAND.license} label="License" />
            </li>
            <li>
              <ResourceLink href={BRAND.supportUrl} label="Support" />
            </li>
            <li>
              <ResourceLink href={BRAND.url} label="Product home" />
            </li>
          </ul>
        </div>
      </div>

      <div className="account-row">
        <span className="account-label">Provenance record</span>
        <div className="account-row-main">
          <div className="about-unit-manifest-controls">
            <button
              type="button"
              className="about-unit-disclosure-trigger"
              aria-expanded={showRaw}
              onClick={() => setShowRaw((v) => !v)}
            >
              <span
                className={cx("about-unit-caret", showRaw && "open")}
                aria-hidden="true"
              >
                ›
              </span>
              View JSON-LD
            </button>
            {showRaw ? (
              <CopyButton data={raw} title="Copy the JSON-LD record">
                Copy
              </CopyButton>
            ) : null}
          </div>
          <div className={cx("about-unit-disclosure", showRaw && "open")}>
            <div className="about-unit-disclosure-inner">
              <pre className="about-unit-code">{raw}</pre>
              <p className="account-muted">
                The same <span className="about-unit-mono">schema.org/SoftwareApplication</span>{" "}
                record embedded in this page&rsquo;s <span className="about-unit-mono">&lt;head&gt;</span>.
              </p>
            </div>
          </div>
        </div>
      </div>

      <div className="account-row">
        <span className="account-label">Diagnostics</span>
        <div className="account-row-main">
          <div className="about-unit-manifest-controls">
            <span className="about-unit-mono">
              {incidents.length === 0
                ? "No recent failures"
                : `${incidents.length} recent failure${incidents.length === 1 ? "" : "s"}`}
            </span>
            <CopyButton
              // Lazy: `captured` must stamp the ask, not this pane's last draw.
              data={() => formatDiagnostics({ version })}
              title="Copy the last 24 hours of failed requests"
              disabled={incidents.length === 0}
            >
              Copy diagnostics
            </CopyButton>
          </div>
          <p className="account-muted">
            The last 24 hours of failed requests — ids, codes and paths only, never
            measurements or prompt text. Paste it into a bug report: each row carries the{" "}
            <span className="about-unit-mono">error_id</span> that names the matching line
            in the server log.
          </p>
        </div>
      </div>
    </>
  );
}

function OrgLine({ org }: { org: { name: string; url: string } }) {
  return (
    <span className="about-unit-org">
      <span className="about-unit-org-name">{org.name}</span>
      <a className="about-unit-org-link" href={org.url} target="_blank" rel="noreferrer">
        {prettyHost(org.url)} ↗
      </a>
    </span>
  );
}

function ResourceLink({ href, label }: { href: string; label: string }) {
  return (
    <a className="about-unit-resource" href={href} target="_blank" rel="noreferrer">
      {label}
      <span aria-hidden="true"> ↗</span>
    </a>
  );
}

// `self-declared` is a neutral state, NOT a success state.
function ProvenancePill({ verified }: { verified: boolean }) {
  return (
    <span className={cx("about-unit-pill", verified ? "verified" : "declared")}>
      <span className="about-unit-pill-icon" aria-hidden="true">
        {verified ? "✓" : "ⓘ"}
      </span>
      {verified ? "Verified" : "Self-declared"}
    </span>
  );
}

function Mark() {
  return <PotterMark size={22} />;
}

function prettyHost(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}
