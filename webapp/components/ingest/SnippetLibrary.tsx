"use client";

import { Fragment, useState } from "react";
import { libraryRead, type DraftPatch, type LibrarySnippet, type NodePrompt } from "@/lib/api";
import { useRead } from "@/lib/hooks/useRead";
import { promptFieldLabel, promptFieldsPatch, withSnippet } from "@/lib/prompt-fields";
import type { NodeScope } from "@/lib/SelectionContext";
import { endSnippetDrag, startSnippetDrag } from "@/lib/state/snippet-drag";
import {
  Badge,
  Button,
  Chip,
  ChipGroup,
  Empty,
  Menu,
  MenuItem,
  MenuSep,
  ReadState,
  Term,
  Toolbar,
  ToolbarSpacer,
} from "@/components/ui";

// One prompt node a snippet can land in: its declared fields and the prompts map its patch is built over.
export interface PromptTarget {
  scope: NodeScope;
  node: string;
  fields: readonly string[];
  prompts: Record<string, NodePrompt>;
}

type Part = LibrarySnippet["suits"][number];

export function SnippetLibrary({
  targets,
  onApply,
}: {
  targets: PromptTarget[];
  onApply: (patch: DraftPatch) => void;
}) {
  const read = useRead(libraryRead());
  const [query, setQuery] = useState("");
  const [part, setPart] = useState<Part | null>(null);

  return (
    <section className="snippet-library" aria-label="Snippet library">
      <Toolbar className="snippet-library-bar">
        <Term
          className="snippet-library-title"
          content="Snippets from the block packs installed here. Drag one onto a prompt field, or pick it from Insert into…; a field the snippet suits lights up, and a drop anywhere else is allowed and shown as a miss."
        >
          Snippet library
        </Term>
        <ToolbarSpacer />
        <input
          type="search"
          className="snippet-library-search"
          placeholder="Search text and tags…"
          aria-label="Search snippets by text or tag"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </Toolbar>
      <ReadState read={read} subject="the snippet library" className="mech-empty">
        {(library) => {
          const q = query.trim().toLowerCase();
          const shown = library.snippets.filter(
            (s) =>
              (part === null || s.suits.includes(part)) &&
              (q === "" ||
                s.text.toLowerCase().includes(q) ||
                s.tags.some((t) => t.toLowerCase().includes(q))),
          );
          return (
            <>
              <ChipGroup label="Filter snippets by the part they suit">
                {library.parts.map((p) => (
                  <Chip key={p} on={part === p} onClick={() => setPart(part === p ? null : p)}>
                    {promptFieldLabel(p)}
                  </Chip>
                ))}
              </ChipGroup>
              {library.snippets.length === 0 ? (
                <Empty className="mech-empty">
                  No block pack installed here holds a snippet yet.
                </Empty>
              ) : shown.length === 0 ? (
                <Empty className="mech-empty">No snippet matches.</Empty>
              ) : (
                <ul className="snippet-list">
                  {shown.map((s) => (
                    <SnippetRow
                      key={`${s.pack}/${s.id}`}
                      snippet={s}
                      targets={targets}
                      onApply={onApply}
                    />
                  ))}
                </ul>
              )}
            </>
          );
        }}
      </ReadState>
    </section>
  );
}

function SnippetRow({
  snippet: s,
  targets,
  onApply,
}: {
  snippet: LibrarySnippet;
  targets: PromptTarget[];
  onApply: (patch: DraftPatch) => void;
}) {
  const insert = (t: PromptTarget, field: string) => {
    const own = t.prompts[t.node];
    // An empty field is absent from a served prompt, so a miss reads as empty text.
    const text = (f: string) => own?.fields[f] ?? "";
    const fields = Object.fromEntries(
      t.fields.map((f) => [f, f === field ? withSnippet(text(f), s.text) : text(f)]),
    );
    onApply(promptFieldsPatch(t.scope, t.node, t.prompts, fields));
  };
  return (
    <li
      className="snippet"
      draggable
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = "copy";
        e.dataTransfer.setData("text/plain", s.text);
        startSnippetDrag(s);
      }}
      onDragEnd={endSnippetDrag}
    >
      <p className="snippet-text">{s.text}</p>
      <div className="snippet-meta">
        {s.suits.map((p) => (
          <Badge key={p}>{promptFieldLabel(p)}</Badge>
        ))}
        {s.tags.map((t) => (
          <small key={t} className="snippet-tag">
            #{t}
          </small>
        ))}
        <small className="snippet-source">
          {s.source} · {s.pack}
        </small>
        <Menu
          renderTrigger={({ open, toggle }) => (
            <Button
              variant="ghost"
              aria-haspopup="menu"
              aria-expanded={open}
              disabled={targets.length === 0}
              onClick={toggle}
            >
              Insert into…
            </Button>
          )}
        >
          {({ close }) =>
            targets.map((t, i) => (
              <Fragment key={`${t.scope}/${t.node}`}>
                {i > 0 ? <MenuSep /> : null}
                {t.fields.map((field) => {
                  const suited = s.suits.some((p) => p === field);
                  return (
                    <MenuItem
                      key={field}
                      onClick={() => {
                        insert(t, field);
                        close();
                      }}
                    >
                      {t.scope === "optimizer" ? "Optimizer" : "Target"} · {t.node} ·{" "}
                      {promptFieldLabel(field)}
                      {suited ? " ✓" : " — not a part it suits"}
                    </MenuItem>
                  );
                })}
              </Fragment>
            ))
          }
        </Menu>
      </div>
    </li>
  );
}
