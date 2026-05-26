"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  postArchiveCampaign,
  postDeleteCampaign,
  postUnarchiveCampaign,
  type CampaignSummary,
} from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { Modal, type ModalAction } from "@/components/shell/Modal";

// Per-campaign three-dots menu. Surfaces the lifecycle commands wired in
// `mutations.ts`: archive / unarchive (soft-mark, reversible) and delete
// (soft-mark, hidden from default surface — measurements survive
// cross-campaign cache-hits per ADR-0002 §0.5; nothing on disk is removed).
// Delete asks for confirmation; archive / unarchive fire immediately.

interface Props {
  campaign: CampaignSummary;
}

export function CampaignMenu({ campaign }: Props) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Click-outside + Escape close the dropdown. Mounted only while open so
  // we don't pay the listener cost for every row in a long campaign list.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!containerRef.current) return;
      if (!containerRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const runArchive = useCallback(async () => {
    setOpen(false);
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
    setOpen(false);
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
    <div className="campaign-menu" ref={containerRef}>
      <button
        type="button"
        className="campaign-menu-trigger"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
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
      {open && (
        <div role="menu" className="campaign-menu-list">
          {archived ? (
            <button
              type="button"
              role="menuitem"
              className="campaign-menu-item"
              onClick={(e) => {
                e.stopPropagation();
                void runUnarchive();
              }}
            >
              Unarchive
            </button>
          ) : (
            <button
              type="button"
              role="menuitem"
              className="campaign-menu-item"
              onClick={(e) => {
                e.stopPropagation();
                void runArchive();
              }}
            >
              Archive
            </button>
          )}
          <button
            type="button"
            role="menuitem"
            className="campaign-menu-item campaign-menu-item-danger"
            onClick={(e) => {
              e.stopPropagation();
              setOpen(false);
              setConfirmDelete(true);
            }}
          >
            Delete
          </button>
        </div>
      )}
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
    </div>
  );
}
