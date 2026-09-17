"use client";
import { useId, useState } from "react";
import {
  postArchiveCampaign,
  postDeleteCampaign,
  postSetCampaignLabel,
  postUnarchiveCampaign,
  type CampaignSummary,
} from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import { useWorkspace } from "@/lib/workspace";
import { Modal, type ModalAction } from "@/components/shell/Modal";
import { Button, Dialog, Menu, MenuItem } from "@/components/ui";

// Per-campaign three-dots menu. Surfaces the lifecycle commands wired in
// `mutations.ts`: archive / unarchive (a `lifecycle_status` flip that hides the
// campaign from the listing, moving nothing) and delete (DESTRUCTIVE — removes the campaign tree,
// no recovery; the cross-campaign measurement cache survives per ADR-0002 §0.5
// so siblings still cache-hit), plus rename, the one edit that leaves the tree
// where it is. Delete and rename ask first; archive / unarchive fire
// immediately. Open/close, click-outside, ESC and a row's click swallowing all come from `Menu`.

interface Props {
  campaign: CampaignSummary;
  // `row` — inside a clickable sidebar row: the ROW is the tab stop, so the ⋯
  // takes no focus of its own and swallows the click that would select the row.
  // `standalone` — a top-level control (the app bar), which wants both.
  variant?: "row" | "standalone";
}

export function CampaignMenu({ campaign, variant = "row" }: Props) {
  const inRow = variant === "row";
  // Four verbs, one slot — the menu offers them one at a time, so a second in flight is a
  // double click rather than a second intent.
  const cmd = useCommand<"archive" | "unarchive" | "rename" | "delete">("campaign-menu");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState("");
  const renameFormId = useId();
  // The ROOT hop's campaign — a drilled-in inner leaf still belongs to the campaign
  // being deleted, so this is the id that decides whether the view is affected.
  const { campaignId: viewedCampaignId, followActive } = useWorkspace();

  const runArchive = () =>
    void cmd.run("archive", () => postArchiveCampaign(campaign.campaign_id));

  const runUnarchive = () =>
    void cmd.run("unarchive", () => postUnarchiveCampaign(campaign.campaign_id));

  // The label is display only — `campaignDisplayName` prefers it over the dataset
  // name, and clearing it restores that fallback. Nothing addresses a campaign by
  // it, so there is no view to reconcile here the way delete has to.
  const runRename = () => {
    setRenaming(false);
    void cmd.run("rename", () => postSetCampaignLabel(campaign.campaign_id, draft.trim()));
  };

  const runDelete = () => {
    setConfirmDelete(false);
    void cmd.run(
      "delete",
      () => postDeleteCampaign(campaign.campaign_id),
      // Deleting what you are looking at is the ordinary case (root CLAUDE.md), so the view
      // must let go of it BEFORE the re-poll rather than wait to discover it by 404. The
      // reconciliation in `workspace.tsx` is the safety net for every other way an address
      // dies; this is the one we authored and can act on immediately.
      () => {
        if (viewedCampaignId === campaign.campaign_id) followActive();
      },
    );
  };

  const pending = cmd.pending !== null;
  const archived = campaign.lifecycle_status === "archived";
  const deleteActions: ModalAction[] = [
    { label: "Cancel", onClick: () => setConfirmDelete(false) },
    { label: "Delete", variant: "danger", onClick: runDelete },
  ];

  return (
    <>
      <Menu
        align="right"
        renderTrigger={({ open, toggle }) => (
          <button
            type="button"
            className="campaign-menu-trigger"
            onClick={(e) => {
              e.stopPropagation();
              toggle();
            }}
            aria-haspopup="menu"
            aria-expanded={open}
            aria-label="Campaign actions"
            title="Campaign actions"
            disabled={pending}
            tabIndex={inRow ? -1 : undefined}
          >
            ⋯
          </button>
        )}
      >
        {({ close }) => (
          <>
            <MenuItem
              onClick={() => {
                close();
                cmd.clear();
                setDraft(campaign.label);
                setRenaming(true);
              }}
            >
              Rename
            </MenuItem>
            <MenuItem
              onClick={() => {
                close();
                if (archived) runUnarchive();
                else runArchive();
              }}
            >
              {archived ? "Unarchive" : "Archive"}
            </MenuItem>
            <MenuItem
              onClick={() => {
                close();
                setConfirmDelete(true);
              }}
            >
              <span className="campaign-menu-danger">Delete</span>
            </MenuItem>
          </>
        )}
      </Menu>
      {cmd.failure && (
        <span className="campaign-menu-err" title={cmd.failure.message}>
          !
        </span>
      )}
      <Dialog
        open={renaming}
        title="Rename campaign"
        onClose={() => setRenaming(false)}
        footer={
          <>
            <Button onClick={() => setRenaming(false)}>Cancel</Button>
            <Button variant="primary" type="submit" form={renameFormId}>
              Save
            </Button>
          </>
        }
      >
        <form
          id={renameFormId}
          onSubmit={(e) => {
            e.preventDefault();
            runRename();
          }}
        >
          <label className="new-campaign-field">
            <span>Name</span>
            <input
              type="text"
              value={draft}
              maxLength={200}
              placeholder={campaign.dataset_name}
              onChange={(e) => setDraft(e.target.value)}
            />
          </label>
        </form>
        {/* The id nothing renames — it addresses the directory, the measurement
            cache and every bookmark. Shown so the operator can still copy it. */}
        <p className="campaign-rename-id">{campaign.campaign_id}</p>
      </Dialog>
      <Modal
        open={confirmDelete}
        title="Delete this campaign?"
        message={`This permanently removes "${
          campaign.label || campaign.campaign_id
        }" — its cycles, rounds, and telemetry — from disk. There is no recovery. The shared measurement cache survives, so other campaigns still cache-hit. (To archive instead of delete, use Archive — it hides the campaign from the list and keeps everything on disk, restorable later.)`}
        actions={deleteActions}
        onClose={() => setConfirmDelete(false)}
      />
    </>
  );
}
