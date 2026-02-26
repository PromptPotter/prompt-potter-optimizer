"""Campaign package — feedback cycle, campaign init, and campaign registry.

Re-exports every public name so callers use one import path::

    from api.services.campaign import run_feedback_cycle, CycleConfig, ...
"""

# feedback_cycle
from api.services.campaign.feedback_cycle import (
    CycleConfig,
    CycleResult,
    CycleRoundResult,
    cycle_config_identity,
    run_feedback_cycle,
)

# campaign_init
from api.services.campaign.campaign_init import (
    init_services,
)

# campaign_registry
from api.services.campaign.campaign_registry import (
    complete_campaign,
    create_campaign,
    generate_campaign_id,
    generate_trial_id,
    get_campaign_lineage,
    record_campaign_rounds,
    record_trial,
)

__all__ = [
    # feedback_cycle
    "CycleConfig",
    "CycleResult",
    "CycleRoundResult",
    "cycle_config_identity",
    "run_feedback_cycle",
    # campaign_init
    "init_services",
    # campaign_registry
    "complete_campaign",
    "create_campaign",
    "generate_campaign_id",
    "generate_trial_id",
    "get_campaign_lineage",
    "record_campaign_rounds",
    "record_trial",
]
