"use client";
import { useCallback, useId, useState } from "react";
import {
  postArchiveCampaign,
  postDeleteCampaign,
  postSetCampaignLabel,
  postUnarchiveCampaign,
  type CampaignSummary,
} from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { useWorkspace } from "@/lib/workspace";
import { Modal, type ModalAction } from "@/components/shell/Modal";
import { Button, Dialog, Popover } from "@/components/ui";

// Per-campaign three-dots menu. Surfaces the lifecycle commands wired in
// `mutations.ts`: archive / unarchive (a `lifecycle_status` flip that hides the
// campaign from the listing, moving nothing) and delete (DESTRUCTIVE — removes the campaign tree,
// no recovery; the cross-campaign measurement cache survives per ADR-0002 §0.5
// so siblings still cache-hit), plus rename, the one edit that leaves the tree
// where it is. Delete and rename ask first; archive / unarchive fire
// immediately. Dropdown open/close + click-outside/ESC come from the Popover.

interface Props {
  campaign: CampaignSummary;
  // `row` — inside a clickable sidebar row: the ROW is the tab stop, so the ⋯
  // takes no focus of its own and swallows the click that would select the row.
  // `standalone` — a top-level control (the app bar), which wants both.
  variant?: "row" | "standalone";
}

export function CampaignMenu({ campaign, variant = "row" }: Props) {
  const inRow = variant === "row";
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState("");
  const renameFormId = useId();
  // The ROOT hop's campaign — a drilled-in inner leaf still belongs to the campaign
  // being deleted, so this is the id that decides whether the view is affected.
  const { campaignId: viewedCampaignId, followActive } = useWorkspace();

  const runArchive = useCallback(async () => {
    setPending(true);
    setErr(null);
    try {
      await postArchiveCampaign(campaign.campaign_id);
      bumpRevalidation();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setPending(false);
    }
  }, [campaign.campaign_id]);

  const runUnarchive = useCallback(async () => {
    setPending(true);
    setErr(null);
    try {
      await postUnarchiveCampaign(campaign.campaign_id);
      bumpRevalidation();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setPending(false);
    }
  }, [campaign.campaign_id]);

  // The label is display only — `campaignDisplayName` prefers it over the dataset
  // name, and clearing it restores that fallback. Nothing addresses a campaign by
  // it, so there is no view to reconcile here the way delete has to.
  const runRename = useCallback(async () => {
    setRenaming(false);
    setPending(true);
    setErr(null);
    try {
      await postSetCampaignLabel(campaign.campaign_id, draft.trim());
      bumpRevalidation();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setPending(false);
    }
  }, [campaign.campaign_id, draft]);

  const runDelete = useCallback(async () => {
    setConfirmDelete(false);
    setPending(true);
    setErr(null);
    try {
      await postDeleteCampaign(campaign.campaign_id);
      // Deleting what you are looking at is the ordinary case (root CLAUDE.md), so
      // the view must let go of it HERE rather than wait to discover it by 404. The
      // reconciliation in `workspace.tsx` is the safety net for every other way an
      // address dies; this is the one we authored and can act on immediately.
      if (viewedCampaignId === campaign.campaign_id) followActive();
      bumpRevalidation();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setPending(false);
    }
  }, [campaign.campaign_id, viewedCampaignId, followActive]);

  const archived = campaign.lifecycle_status === "archived";
  const deleteActions: ModalAction[] = [
    { label: "Cancel", onClick: () => setConfirmDelete(false) },
    { label: "Delete", variant: "danger", onClick: () => void runDelete() },
  ];

  return (
    <>
      <Popover
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
          <div role="menu">
            <button
              type="button"
              role="menuitem"
              className="campaign-menu-item"
              onClick={(e) => {
                e.stopPropagation();
                close();
                setErr(null);
                setDraft(campaign.label);
                setRenaming(true);
              }}
            >
              Rename
            </button>
            <button
              type="button"
              role="menuitem"
              className="campaign-menu-item"
              onClick={(e) => {
                e.stopPropagation();
                close();
                void (archived ? runUnarchive() : runArchive());
              }}
            >
              {archived ? "Unarchive" : "Archive"}
            </button>
            <button
              type="button"
              role="menuitem"
              className="campaign-menu-item campaign-menu-item-danger"
              onClick={(e) => {
                e.stopPropagation();
                close();
                setConfirmDelete(true);
              }}
            >
              Delete
            </button>
          </div>
        )}
      </Popover>
      {err && (
        <span className="campaign-menu-err" title={err}>
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
            void runRename();
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
