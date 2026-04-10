# AIME 2025 — Dataset Context

## Type

`backend` — uses the `llm_only` step for LLM-based evaluation.

## Prerequisites

- TermNorm backend must be running: `curl -s http://127.0.0.1:8000/status`
- If backend is down, tell the user: "Start the TermNorm backend first, then re-run `/potter-run`"

## Init Flags

```
--backend-url http://127.0.0.1:8000
--backend-id aime_2025
--config datasets/aime_2025/campaign.json
--skip-baseline
```

## Data

- Source: HuggingFace `MathArena/aime_2025` (30 problems from AIME I and II 2025)
- Format: competition math problem -> integer answer in [0, 999]
- campaign.json uses `sp_budget_ttest: 0` (all 30 — dataset is tiny)

## Cycle Identity

Default campaign.json uses experiment mode (no `strict_cycle_identity`). You can freely switch between `--round` and full loop, adjust patience, and interrupt/resume without losing campaign history. For publication runs, add `"strict_cycle_identity": true` to `campaign.json` to lock all parameters into the cycle identity.

## Pipeline Notes

- Pipeline: `llm_only` step only — prompt flows through `pipeline_params` via PromptTemplate
- Optimization target: prompt template (reasoning strategy, verification steps, etc.)
- max_tokens: 4000 (competition math needs longer reasoning chains)

## End-to-End Test

Clean-slate test to verify the full optimization loop, interrupt/resume, and cache behavior.

```bash
# 1. Clean slate
rm -rf .promptpotter/projects/aime_2025

# 2. Verify backend is up
curl -s http://127.0.0.1:8000/status

# 3. Init campaign
python -m promptpotter.cli.campaign_runner init \
    --backend-url http://127.0.0.1:8000 \
    --backend-id aime_2025 \
    --config datasets/aime_2025/campaign.json \
    --skip-baseline

# 4. Set task description
python -m promptpotter.cli.campaign_runner set-task \
    --task-file datasets/aime_2025/task_description.md

# 5. Run one complete round (~10 min: 6 candidates × 30 queries)
#    Should: generate candidates → evaluate all → critique → checkpoint → stop
python -m promptpotter.cli.campaign_runner optimize --round

# 6. Verify round completed with critique
python -m promptpotter.cli.campaign_runner show-results

# 7. Interrupt test: start round 2, Ctrl+C after a few candidates finish
python -m promptpotter.cli.campaign_runner optimize --round
# (Ctrl+C after ~2 min)

# 8. Resume: completed candidates should cache-hit (0.0s), rest continue fresh
python -m promptpotter.cli.campaign_runner optimize --round

# 9. Switch to full loop — same cycle, no data loss, continues from round 3+
python -m promptpotter.cli.campaign_runner optimize
```

### What to verify

| Step | Expected |
|------|----------|
| 5 | All 30 queries per candidate (no intractability filter). Round ends with critique text and checkpoint. |
| 6 | Shows round 0 winner, accuracy, candidate scores, critique output |
| 7 | Partial progress — some candidates complete, one mid-evaluation |
| 8 | Completed candidates show `Full-run cache hit` in logs (0.0s). Partial/missing candidates re-evaluate. Same cycle_id. |
| 9 | Same cycle_id. Resumes from round after last completed. No regeneration of prior rounds' candidates. |
