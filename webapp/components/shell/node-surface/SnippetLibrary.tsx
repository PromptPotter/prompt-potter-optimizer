"use client";

import { useState, type ReactNode } from "react";
import {
  libraryRead,
  type LibraryResponse,
  type LibrarySnippet,
} from "@/lib/api";
import { useRead } from "@/lib/hooks/useRead";
import { promptFieldLabel } from "@/lib/prompt-fields";
import { endSnippetDrag, startSnippetDrag } from "@/lib/state/snippet-drag";
import {
  Button,
  Chip,
  Empty,
  Menu,
  MenuCheck,
  ReadState,
  Term,
} from "@/components/ui";
import { cx } from "@/lib/cx";

type Facet = "source" | "method" | "pack" | "tag";

// Within a facet any picked value admits a snippet; across facets every facet must.
type Picked = Record<Facet, readonly string[]>;
const NONE: Picked = { source: [], method: [], pack: [], tag: [] };

function valuesOf(s: LibrarySnippet, facet: Facet): readonly string[] {
  if (facet === "source") return [s.source];
  if (facet === "pack") return [s.pack];
  if (facet === "method") return s.method === null ? [] : [s.method];
  return s.tags;
}

// Each facet's values in served order, once.
function facetValues(
  snippets: readonly LibrarySnippet[],
  facet: Facet,
): string[] {
  return [...new Set(snippets.flatMap((s) => valuesOf(s, facet)))];
}

const FACET_LABEL: Record<Facet, string> = {
  source: "Source",
  method: "Method",
  pack: "Pack",
  tag: "Tags",
};

// The library docked under one node's prompt: a tap lands a snippet in `field`; with no `onPick`
// (a banked prompt) it is browsed only.
export function SnippetLibrary({
  field,
  onPick,
}: {
  field: string;
  onPick: ((snippet: LibrarySnippet) => void) | undefined;
}) {
  const read = useRead(libraryRead());
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Picked>(NONE);
  // The part the insert field accepts leads: a new field re-filters to its own part.
  const [part, setPart] = useState<string | null>(field);
  const [prevField, setPrevField] = useState(field);
  if (field !== prevField) {
    setPrevField(field);
    setPart(field);
  }
  const [limit, setLimit] = useState(PAGE);
  const filterKey = JSON.stringify([query, picked, part]);
  const [prevKey, setPrevKey] = useState(filterKey);
  if (filterKey !== prevKey) {
    setPrevKey(filterKey);
    setLimit(PAGE);
  }
  const toggle = (facet: Facet, v: string) =>
    setPicked((p) => ({
      ...p,
      [facet]: p[facet].includes(v)
        ? p[facet].filter((x) => x !== v)
        : [...p[facet], v],
    }));

  return (
    <section className="snippet-library" aria-label="Snippet library">
      <ReadState
        read={read}
        subject="the snippet library"
        className="mech-empty"
      >
        {(library) => {
          // A source the served `sources` misses still names itself by its id.
          const sourceTitle = (id: string) => {
            const source = library.sources.find((x) => x.id === id);
            return source === undefined ? id : source.short_title;
          };
          const label = (facet: Facet, v: string) =>
            facet === "source" ? sourceTitle(v) : v;
          // A field no snippet part names (a `plan`) filters nothing.
          const partOn = library.parts.find((p) => p === part) ?? null;
          const q = query.trim().toLowerCase();
          const matching = library.snippets.filter(
            (s) =>
              (partOn === null || s.suits.includes(partOn)) &&
              (Object.keys(picked) as Facet[]).every(
                (f) =>
                  picked[f].length === 0 ||
                  valuesOf(s, f).some((v) => picked[f].includes(v)),
              ) &&
              (q === "" ||
                s.text.toLowerCase().includes(q) ||
                s.tags.some((t) => t.toLowerCase().includes(q))),
          );
          const shown = matching.slice(0, limit);
          const active: { key: string; text: string; clear: () => void }[] = [
            ...(partOn === null
              ? []
              : [
                  {
                    key: "part",
                    text: promptFieldLabel(partOn),
                    clear: () => setPart(null),
                  },
                ]),
            ...(Object.keys(picked) as Facet[]).flatMap((f) =>
              picked[f].map((v) => ({
                key: `${f}:${v}`,
                text: label(f, v),
                clear: () => toggle(f, v),
              })),
            ),
          ];
          return (
            <>
              <div className="snippet-filters">
                <div className="snippet-head">
                  <svg className="snippet-glyph" viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H9v16H5.5A1.5 1.5 0 0 1 4 18.5zM10 4h3v16h-3zM14.2 5.1l2.9-.8 3.7 14.5-2.9.8z" />
                  </svg>
                  <label className="snippet-search">
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <circle cx="11" cy="11" r="6.5" />
                      <path d="m16 16 4.5 4.5" />
                    </svg>
                    <input
                      type="search"
                      className="snippet-library-search"
                      placeholder="Snippets"
                      aria-label="Search snippets by text or tag"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                    />
                  </label>
                </div>
                <div
                  className="snippet-facets"
                  role="group"
                  aria-label="Filter by"
                >
                  <FacetMenu label="Part" count={partOn === null ? 0 : 1}>
                    {library.parts.map((p) => (
                      <MenuCheck
                        key={p}
                        on={partOn === p}
                        onClick={() => setPart(partOn === p ? null : p)}
                      >
                        {promptFieldLabel(p)}
                        {p === field ? " · this field" : ""}
                      </MenuCheck>
                    ))}
                  </FacetMenu>
                  {(["source", "method", "pack", "tag"] as const).map((f) => (
                    <FacetMenu
                      key={f}
                      label={FACET_LABEL[f]}
                      count={picked[f].length}
                    >
                      {facetValues(library.snippets, f).map((v) => (
                        <MenuCheck
                          key={v}
                          on={picked[f].includes(v)}
                          onClick={() => toggle(f, v)}
                        >
                          {f === "source" ? (
                            <SourceLabel library={library} id={v} />
                          ) : (
                            v
                          )}
                        </MenuCheck>
                      ))}
                    </FacetMenu>
                  ))}
                </div>
              </div>
              <div
                className="snippet-active"
                role="group"
                aria-label="Filters applied"
              >
                {active.map((a) => (
                  <Chip
                    key={a.key}
                    on
                    onClick={a.clear}
                    ariaLabel={`Remove filter ${a.text}`}
                  >
                    {a.text} ×
                  </Chip>
                ))}
                <span className="snippet-count" aria-live="polite">
                  {matching.length}/{library.snippets.length}
                  {onPick ? (
                    <>
                      {" "}
                      → <strong>{promptFieldLabel(field)}</strong>
                    </>
                  ) : null}
                </span>
              </div>
              {library.snippets.length === 0 ? (
                <Empty className="mech-empty">
                  No block pack installed here holds a snippet yet.
                </Empty>
              ) : matching.length === 0 ? (
                <Empty className="mech-empty">
                  No snippet matches these filters.
                </Empty>
              ) : (
                <ul className="snippet-list">
                  {shown.map((s) => (
                    <SnippetRow
                      key={`${s.pack}/${s.id}`}
                      snippet={s}
                      field={field}
                      source={sourceTitle(s.source)}
                      onPick={onPick}
                    />
                  ))}
                  {shown.length < matching.length ? (
                    <li>
                      <Button
                        variant="ghost"
                        onClick={() => setLimit(limit + PAGE)}
                      >
                        Show {Math.min(PAGE, matching.length - shown.length)}{" "}
                        more
                      </Button>
                    </li>
                  ) : null}
                </ul>
              )}
            </>
          );
        }}
      </ReadState>
    </section>
  );
}

const PAGE = 20;
// Past this the text clamps to two lines, and the row offers its whole text.
const CLAMP_CHARS = 140;

function FacetMenu({
  label,
  count,
  children,
}: {
  label: string;
  count: number;
  children: ReactNode;
}) {
  return (
    <Menu
      align="left"
      renderTrigger={({ open, toggle }) => (
        <Button
          variant="ghost"
          className={cx("snippet-facet", count > 0 && "is-on")}
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={toggle}
        >
          {label}
          {count > 0 ? ` ${count}` : ""} ▾
        </Button>
      )}
    >
      {() => children}
    </Menu>
  );
}

function SourceLabel({
  library,
  id,
}: {
  library: LibraryResponse;
  id: string;
}) {
  const src = library.sources.find((x) => x.id === id);
  if (!src || src.first_author === null) return <>{src?.title ?? id}</>;
  return (
    <span className="snippet-source-option">
      {src.short_title}
      <small>
        {src.first_author} {src.year} · {src.id}
      </small>
    </span>
  );
}

function SnippetRow({
  snippet: s,
  field,
  source,
  onPick,
}: {
  snippet: LibrarySnippet;
  field: string;
  source: string;
  onPick: ((snippet: LibrarySnippet) => void) | undefined;
}) {
  const [whole, setWhole] = useState(false);
  const long = s.text.length > CLAMP_CHARS || s.text.includes("\n");
  const suited = s.suits.some((p) => p === field);
  const text = (
    <span className={cx("snippet-text", long && !whole && "is-clamped")}>
      {s.text}
    </span>
  );
  return (
    <li
      className={cx("snippet", onPick && !suited && "is-miss")}
      draggable={onPick != null}
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = "copy";
        e.dataTransfer.setData("text/plain", s.text);
        startSnippetDrag(s);
      }}
      onDragEnd={endSnippetDrag}
    >
      {onPick ? (
        <button
          type="button"
          className="snippet-pick"
          aria-label={`Add to ${promptFieldLabel(field)}: ${s.text.slice(0, 80)}`}
          onClick={() => onPick(s)}
        >
          {text}
          <span className="snippet-add" aria-hidden="true">
            +
          </span>
        </button>
      ) : (
        text
      )}
      <span className="snippet-meta">
        {suited || !onPick ? null : (
          <Term
            className="snippet-miss"
            content={`Written for ${s.suits.map(promptFieldLabel).join(", ")}; it still lands in ${promptFieldLabel(field)} if you tap it.`}
          >
            not a {promptFieldLabel(field)} part
          </Term>
        )}
        <span className="snippet-source">
          {source}
          {s.method ? ` · ${s.method}` : ""} · {s.pack}
        </span>
        {long ? (
          <Button
            variant="ghost"
            className="snippet-more"
            aria-expanded={whole}
            onClick={() => setWhole(!whole)}
          >
            {whole ? "Less" : "More"}
          </Button>
        ) : null}
      </span>
    </li>
  );
}
