"use client";
import { useCallback, useState } from "react";
import {
  postArchiveCampaign,
  postDeleteCampaign,
  postUnarchiveCampaign,
  type CampaignSummary,
} from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { Modal, type ModalAction } from "@/components/shell/Modal";
import { Popover } from "@/components/ui";

// Per-campaign three-dots menu. Surfaces the lifecycle commands wired in
// `mutations.ts`: archive / unarchive (soft-mark, reversible) and delete
// (soft-mark, hidden from default surface — measurements survive
// cross-campaign cache-hits per ADR-0002 §0.5; nothing on disk is removed).
// Delete asks for confirmation; archive / unarchive fire immediately.
// Dropdown open/close + click-outside/ESC come from the Popover primitive.

interface Props {
  campaign: CampaignSummary;
}

export function CampaignMenu({ campaign }: Props) {
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

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

  const runDelete = useCallback(async () => {
    setConfirmDelete(false);
    setPending(true);
    setErr(null);
    try {
      await postDeleteCampaign(campaign.campaign_id);
      bumpRevalidation();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setPending(false);
    }
  }, [campaign.campaign_id]);

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
            tabIndex={-1}
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
      <Modal
        open={confirmDelete}
        title="Delete this campaign?"
        message={`This soft-marks "${
          campaign.label || campaign.campaign_id
        }" as deleted and drops it from the default sidebar. Measurements survive on disk — cross-campaign cache-hits keep working — and the campaign is reachable by id from the file tree. This is not a physical delete.`}
        actions={deleteActions}
        onClose={() => setConfirmDelete(false)}
      />
    </>
  );
}
