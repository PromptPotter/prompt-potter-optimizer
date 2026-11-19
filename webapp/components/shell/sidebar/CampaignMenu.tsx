import { useId, useState } from "react";
import { commands } from "@/lib/api";
import type { ListedCampaign } from "@/lib/api/types";
import { tagValue } from "@/lib/derivations";
import { useCommand } from "@/lib/hooks/useCommand";
import { useIngest } from "@/lib/state/ingest-flow";
import { useWorkspace } from "@/lib/state/workspace";
import { Button, Dialog, FailureLine, Menu, MenuItem, Switch } from "@/components/ui";

export function CampaignMenu({ campaign }: { campaign: ListedCampaign }) {
  const cmd = useCommand<"archive" | "unarchive" | "rename" | "delete">("campaign-menu", campaign.address);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [keepResults, setKeepResults] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState("");
  const renameFormId = useId();
  // The ROOT hop: a drilled-in inner leaf still belongs to the campaign being deleted.
  const { viewed, navigate } = useWorkspace();
  const viewedCampaignId = viewed?.root.campaignId ?? null;
  const flow = useIngest();
  // An unstarted campaign has no run to rename or archive: Delete is its one action.
  const unstarted = campaign.entry_kind === "unstarted";

  const runArchive = () =>
    void cmd.run("archive", () =>
      commands["archive-campaign"]({ campaign_id: campaign.address }),
    );

  const runUnarchive = () =>
    void cmd.run("unarchive", () =>
      commands["unarchive-campaign"]({ campaign_id: campaign.address }),
    );

  const runRename = () => {
    setRenaming(false);
    // `""` is a real value: it clears the label back to the dataset-name fallback.
    void cmd.run("rename", () =>
      commands["rename-campaign"]({
        campaign_id: campaign.address,
        label: draft.trim(),
      }),
    );
  };

  // The listing serves what Delete files: the dialog presses it and decides nothing.
  const act = campaign.delete;
  const runDelete = () => {
    setConfirmDelete(false);
    if (act.kind === "discard-draft") {
      void cmd.run(
        "delete",
        () => commands["discard-draft"]({ draft_id: campaign.address }),
        () => flow.discarded(campaign.address),
      );
      return;
    }
    void cmd.run(
      "delete",
      () => commands["delete-campaign"]({ campaign_id: campaign.address, keep_results: keepResults }),
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
            onClick={toggle}
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
        title={`Delete "${campaign.label}"?`}
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
        <p>{act.says}</p>
        {act.keep_results !== null && (
          <p>
            <Switch label="Keep its results" checked={keepResults} onChange={() => setKeepResults(!keepResults)} />
            Keep its results: {act.keep_results}
          </p>
        )}
      </Dialog>
    </>
  );
}
