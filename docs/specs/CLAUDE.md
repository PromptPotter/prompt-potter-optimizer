# docs/specs -- Milestone Specs

## How to start a milestone

1. Read the milestone spec — scope decisions, deliverables, wave tables
2. Read the service files listed in the pre-reading hint

| Milestone | Spec file | Pre-reading hint |
|-----------|-----------|-----------------|
| M8: Campaign Intelligence (Complete) | [`m8-campaign-intelligence.md`](m8-campaign-intelligence.md) | `promptpotter/services/eval_gateway.py`, `promptpotter/services/eval_query.py`, `promptpotter/services/search/smart_search.py`, `promptpotter/services/campaign/l1_optimizer.py`, `promptpotter/services/search/search_memory.py` |
| M9: Publication, Stable Config & Webapp | [`m9-publication-config-webapp.md`](m9-publication-config-webapp.md) | `promptpotter/config/optimizer_prompts/`, `promptpotter/config/llm_client.py`, `promptpotter/shared/scoring.py`, `promptpotter/main.py`, `docs/benchmarks.md` |
| Security Foundations | [`security-foundations.md`](security-foundations.md) | `promptpotter/services/campaign/campaign_setup.py`, `promptpotter/services/llm_eval_adapter.py`, `promptpotter/config/settings.py` |

Archived specs: M0-M7 in git history, old M9 (Multi-Connector) at [`archive/m9-multi-connector.md`](archive/m9-multi-connector.md). Entry-point parity docs absorbed into CLAUDE.md § Three-layer I/O architecture.
