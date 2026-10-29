import fs from "node:fs";
import path from "node:path";
import type { APIRequestContext, Page } from "@playwright/test";
import type { DraftCampaignWire } from "@/lib/api/types.generated";
import { test, expect, open, command, datasetNames, drafts, passConsent, type Draft } from "../harness";
import { COLD_HOME } from "../world";

// The check-in writes the overlay `--set nodes.{node}.config.{knob}` writes, so reading the draft's
// `optimization_overrides.nodes` back is reading the terminal's draft.

const PACK = "e2e";

test.describe.configure({ mode: "serial" });

let draft: Draft | null = null;

async function served(request: APIRequestContext): Promise<DraftCampaignWire> {
  const res = await request.get(`/api/v1/drafts/${draft!.draftId}`);
  expect(res.ok(), `GET /drafts/${draft!.draftId} → ${res.status()}`).toBeTruthy();
  return ((await res.json()) as { draft: DraftCampaignWire }).draft;
}

async function optimizerStep(page: Page) {
  await open(page);
  await passConsent(page);
  await page.getByRole("button", { name: "+ New campaign" }).click();
  await page
    .locator(".ingest-picklist-group", { hasText: "Open check-ins" })
    .getByRole("button", { name: new RegExp(`^${draft!.slug}\\b`) })
    .click({ timeout: 60_000 });
  // The optimizer step leads the check-in and starts open; a knob outside `main` sits under All knobs.
  await page.locator("summary", { hasText: "All knobs" }).click();
}

test.afterAll(async ({ request }) => {
  if (draft) await command(request, "discard-draft", { draft_id: draft.draftId });
});

test.describe("the check-in's pools and recombination host", () => {
  test("a dataset opens a draft, and an installed pack seeds its block pool", async ({
    page,
    request,
  }) => {
    test.setTimeout(300_000);
    // None ships, so the walk installs one where `config/paths.py::block_pack_path` resolves it.
    const dir = path.join(COLD_HOME, "block_packs", PACK);
    fs.mkdirSync(dir, { recursive: true });
    const snippet = (text: string) => ({
      text,
      suits: ["instruction"],
      tags: [],
      provenance: {
        kind: "authored",
        author: "e2e",
        date: "2026-10-29",
        experiment: "checkin-pools",
        version: "1",
        notes: null,
      },
    });
    fs.writeFileSync(
      path.join(dir, "pack.json"),
      JSON.stringify({
        works: {},
        snippets: [snippet("Answer briefly."), snippet("Show your steps.")],
      }),
    );

    const known = new Set((await drafts(request)).map((d) => d.draftId));
    await open(page);
    await passConsent(page);
    await page.getByRole("button", { name: "+ New campaign" }).click();
    const [name] = await datasetNames(request);
    await page
      .locator(".ingest-picklist-group", { hasText: "Datasets" })
      .getByRole("button", { name: new RegExp(`^${name}\\b`) })
      .click({ timeout: 60_000 });
    await expect
      .poll(async () => (await drafts(request)).some((d) => !known.has(d.draftId)), {
        message: "picking a dataset opened no draft",
        timeout: 180_000,
      })
      .toBeTruthy();
    draft = (await drafts(request)).find((d) => !known.has(d.draftId))!;

    const patch = {
      optimization_overrides: {
        optimizer: "potter",
        nodes: { block_pool: { config: { packs: [PACK] } } },
      },
    };
    // Opening the check-in fires its resolver turn, which holds the draft (`draft_held`) until it ends.
    let packed = { status: 0, body: "" };
    await expect
      .poll(
        async () => {
          packed = await command(request, "edit-draft-campaign", { draft_id: draft!.draftId, patch });
          return packed.status === 409 && packed.body.includes("draft_held");
        },
        { message: "the resolver turn never released the draft", timeout: 120_000 },
      )
      .toBeFalsy();
    expect(packed.status, `installing ${PACK} on the draft: ${packed.body}`).toBeLessThan(300);
    const pool = (await served(request)).optimizer_setup.pools.find((p) => p.pool === "block_pool");
    const authored = pool?.offered.find((o) => o.path === "instruction/authored");
    expect(authored?.entries, "the pack's two snippets are not offered").toBe(2);
  });

  test("the browser switches the host and narrows the pool, and the draft reads both back", async ({
    page,
    request,
  }) => {
    test.setTimeout(300_000);
    const before = (await served(request)).optimizer_setup.pools.find((p) => p.pool === "block_pool")!;
    const offer = before.offered.find((o) => o.kind === "entry" && o.path.startsWith("instruction."))!;
    const entry = offer.path;
    const chip = { name: new RegExp(`^${offer.label.replace(".", "\\.")}$`) };
    const all = { name: /^All entries / };

    await optimizerStep(page);
    await page.getByRole("combobox", { name: "recombination_host" }).selectOption("l2_context");
    const pool = page.getByRole("group", { name: "block_pool entries in play" });
    await pool.getByRole("button", chip).click();
    // Lit from the served draft, so this waits on the write landing before `*` comes off.
    await expect(pool.getByRole("button", chip)).toHaveAttribute("aria-pressed", "true");
    await pool.getByRole("button", all).click();
    await expect(pool.getByRole("button", all)).toHaveAttribute("aria-pressed", "false");

    const nodes = (await served(request)).optimization_overrides.nodes as Record<
      string,
      { config?: Record<string, unknown> }
    >;
    expect(nodes.l1_generate?.config?.recombination_host).toBe("l2_context");
    expect(nodes.block_pool?.config?.active).toEqual([entry]);

    await optimizerStep(page);
    await expect(page.getByRole("combobox", { name: "recombination_host" })).toHaveValue("l2_context");
    await expect(
      page.getByRole("group", { name: "block_pool entries in play" }).getByRole("button", chip),
    ).toHaveAttribute("aria-pressed", "true");
  });
});
