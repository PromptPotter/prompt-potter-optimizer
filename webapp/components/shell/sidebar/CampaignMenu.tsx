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

// Per-campaign ⋯ menu: archive / unarchive, rename, and delete (destructive; the measurement
// cache survives per ADR-0002).

interface Props {
  campaign: CampaignSummary;
  // `row`: the row is the tab stop, so the ⋯ takes no focus and swallows the row's click.
  variant?: "row" | "standalone";
}

export function CampaignMenu({ campaign, variant = "row" }: Props) {
  const inRow = variant === "row";
  const cmd = useCommand<"archive" | "unarchive" | "rename" | "delete">("campaign-menu");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState("");
  const renameFormId = useId();
  // The ROOT hop: a drilled-in inner leaf still belongs to the campaign being deleted.
  const { campaignId: viewedCampaignId, followActive } = useWorkspace();

  const runArchive = () =>
    void cmd.run("archive", () => postArchiveCampaign(campaign.campaign_id));

  const runUnarchive = () =>
    void cmd.run("unarchive", () => postUnarchiveCampaign(campaign.campaign_id));

  // Display only — nothing addresses a campaign by its label, so no view to reconcile.
  const runRename = () => {
    setRenaming(false);
    void cmd.run("rename", () => postSetCampaignLabel(campaign.campaign_id, draft.trim()));
  };

  const runDelete = () => {
    setConfirmDelete(false);
    void cmd.run(
      "delete",
      () => postDeleteCampaign(campaign.campaign_id),
      // Let go of the view BEFORE the re-poll rather than discover it by 404; `workspace.tsx`
      // reconciles every other way an address dies.
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
        {/* The id nothing renames — it addresses the directory, the cache and every bookmark. */}
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
