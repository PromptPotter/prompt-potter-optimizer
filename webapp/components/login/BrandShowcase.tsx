import { BRAND } from "@/lib/brand";
import { PotterMark } from "@/components/brand/PotterMark";

function siteHost(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
}

// NOT operator chrome, so its palette ignores the theme.
export function BrandShowcase() {
  const { url, title, tagline } = BRAND.marketing;

  return (
    <aside className="login-showcase" aria-label={`About ${title}`}>
      {/* The mark itself is the picture: the 512 cut, masked so a whitelabel accent flows through. */}
      <span className="ls-vessel" aria-hidden="true" />

      <div className="ls-content">
        <div className="ls-eyebrow">
          <PotterMark className="ls-mark" size={26} />
          <span>{title}</span>
        </div>

        <h2 className="ls-headline">
          Fix a broken LLM pipeline <em>in one hour of waiting.</em> Then it just works.
        </h2>
        <p className="ls-lead">{tagline}</p>

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
