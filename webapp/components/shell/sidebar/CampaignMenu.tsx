import { useId, useState } from "react";
import { postCommand, type ListedCampaign } from "@/lib/api";
import { tagValue } from "@/lib/derivations";
import { useCommand } from "@/lib/hooks/useCommand";
import { useIngest } from "@/lib/ingest-flow";
import { useWorkspace } from "@/lib/workspace";
import { Button, Dialog, FailureLine, Menu, MenuItem } from "@/components/ui";

export function CampaignMenu({ campaign }: { campaign: ListedCampaign }) {
  const cmd = useCommand<"archive" | "unarchive" | "rename" | "delete">("campaign-menu");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState("");
  const renameFormId = useId();
  // The ROOT hop: a drilled-in inner leaf still belongs to the campaign being deleted.
  const { campaignId: viewedCampaignId, navigate } = useWorkspace();
  const { flow } = useIngest();
  // An unstarted campaign has no run to rename or archive: Delete is its one action.
  const unstarted = campaign.level === "unstarted";

  const runArchive = () =>
    void cmd.run("archive", () =>
      postCommand("archive-campaign", { campaign_id: campaign.address }),
    );

  const runUnarchive = () =>
    void cmd.run("unarchive", () =>
      postCommand("unarchive-campaign", { campaign_id: campaign.address }),
    );

  const runRename = () => {
    setRenaming(false);
    // `""` is a real value: it clears the label back to the dataset-name fallback.
    void cmd.run("rename", () =>
      postCommand("set-campaign-label", {
        campaign_id: campaign.address,
        label: draft.trim(),
      }),
    );
  };

  const runDelete = () => {
    setConfirmDelete(false);
    // An unstarted campaign is a check-in: its draft is discarded, and the flow lets go of it.
    if (unstarted) {
      void cmd.run(
        "delete",
        () => postCommand("discard-draft", { draft_id: campaign.address }),
        () => flow.discarded(campaign.address),
      );
      return;
    }
    void cmd.run(
      "delete",
      () => postCommand("delete-campaign", { campaign_id: campaign.address }),
      // Lets go of the view BEFORE the re-poll rather than discovering it by 404.
      () => {
        if (viewedCampaignId === campaign.address) navigate(null);
      },
    );
  };

  const pending = cmd.pending !== null;
  const archived = !unstarted && campaign.facts.lifecycle === "archived";

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
            // The ROW is the tab stop: focus here would nest one tab stop in another.
            tabIndex={-1}
          >
            ⋯
          </button>
        )}
      >
        {({ close }) => (
          <>
            {!unstarted && (
              <>
                <MenuItem
                  onClick={() => {
                    close();
                    cmd.clear();
                    setDraft(campaign.facts.label ?? "");
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
              </>
            )}
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
      <FailureLine failure={cmd.failure} className="campaign-menu-err" />
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
          <label className="campaign-rename-field">
            <span>Name</span>
            <input
              type="text"
              value={draft}
              maxLength={200}
              placeholder={tagValue(campaign, "dataset") ?? undefined}
              onChange={(e) => setDraft(e.target.value)}
            />
          </label>
        </form>
        <p className="campaign-rename-id">{campaign.address}</p>
      </Dialog>
      <Dialog
        open={confirmDelete}
        title={unstarted ? "Delete this unstarted campaign?" : "Delete this campaign?"}
        onClose={() => setConfirmDelete(false)}
        footer={
          <>
            <Button onClick={() => setConfirmDelete(false)}>Cancel</Button>
            <Button variant="danger" onClick={runDelete}>
              Delete
            </Button>
          </>
        }
      >
        <p>
          {unstarted
            ? `This permanently removes the "${campaign.label}" check-in — its setup and its upload — from disk. There is no recovery. What the check-in cost stays on the account.`
            : `This permanently removes "${campaign.label}" — its cycles, rounds, and telemetry — from disk. There is no recovery. The shared measurement cache survives, so other campaigns still cache-hit. (To archive instead of delete, use Archive — it hides the campaign from the list and keeps everything on disk, restorable later.)`}
        </p>
      </Dialog>
    </>
  );
}
