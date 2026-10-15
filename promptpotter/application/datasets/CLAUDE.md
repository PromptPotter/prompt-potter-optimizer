# application/datasets/ — loaders, the prompt store, the check-in draft

**`EditDraftPatch` is the ONE origin-edit vocabulary every ingress shares**, so the rules applying it live here (`draft_patch.py`), never in `api/routers/`. **Every pure projection off a draft lives in `draft_campaign.py`** — `draft_build.py` the served ones, `origin_readiness.py` the gate and write-back — because `pipeline_resolve` reads a draft and `jobs/launcher/` calls `pipeline_resolve`: a projection parked in the launcher closes an import cycle.
